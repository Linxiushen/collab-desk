"""Keep one private Codex identity and its opt-in worker with the local server."""
import os
import secrets
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import agent_cli
import store


ROOT = Path(__file__).resolve().parent
ROOM_ID = "codex-workroom"
AGENT_ID = "managed-local-codex"
PROFILE = "local-codex"
NAME = "本地 Codex"
_WORKER = None


def worker_status():
    child = _WORKER
    if child is not None and child.poll() is None:
        return "running"
    return "stopped" if (store.DATA / "managed-profiles" / "enabled").exists() else "disabled"


def ensure_enabled():
    """Return the room ID after provisioning. Requires an initialized store."""
    directory = store.DATA / "managed-profiles"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / (PROFILE + ".json")
    server = f"http://127.0.0.1:{store.PORT}"
    with store.db(True) as db:
        row = db.execute("SELECT id,token_hash FROM agents WHERE name=?", (NAME,)).fetchone()
        if row and row["id"] != AGENT_ID:
            raise RuntimeError("已有手动接入的同名成员，请先为它改名，再打开本地 Codex 工作群。")
        config = agent_cli.load_profile(path) if path.exists() else {}
        token = config.get("token") or ("tz_" + secrets.token_urlsafe(32))
        if not row:
            db.execute("INSERT INTO agents VALUES(?,?,?,'external',?)",
                       (AGENT_ID, NAME, "通过本机 Codex CLI 和本地 Qwen 处理当前群聊中的问题。", store.digest(token)))
        elif row["token_hash"] != store.digest(token):
            db.execute("UPDATE agents SET token_hash=? WHERE id=?", (store.digest(token), AGENT_ID))
        if config != {"server": server, "token": token}:
            agent_cli.save_profile(path, {"server": server, "token": token})
        db.execute("INSERT OR IGNORE INTO rooms VALUES(?,?)", (ROOM_ID, "本地 Codex 工作群"))
        db.execute("INSERT OR IGNORE INTO members VALUES(?,?)", (ROOM_ID, AGENT_ID))
    # Reopening explicitly permits a new attempt if the worker previously exited.
    (directory / "enabled").touch(mode=0o600)
    return ROOM_ID


def worker_env(directory):
    env = os.environ.copy()
    env["PACHAT_LOCAL_PROFILE_DIR"] = str(directory)
    if not shutil.which("codex", path=env.get("PATH", "")):
        # Finder does not always inherit a terminal's PATH.
        paths = [Path("/opt/homebrew/bin"), Path("/usr/local/bin"),
                 Path("/Applications/Codex.app/Contents/Resources"),
                 Path.home() / ".local/bin"]
        for base in [Path("/Applications"), Path.home() / "Applications", Path.home() / "Desktop"]:
            paths.append(base / "ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS")
        for folder in paths:
            if os.access(folder / "codex", os.X_OK):
                env["PATH"] = str(folder) + os.pathsep + env.get("PATH", "")
                break
    return env


def stop_child(child):
    if child.poll() is not None:
        return
    # The worker handles KeyboardInterrupt by terminating its active CLI process group.
    child.send_signal(signal.SIGINT)
    try:
        child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def manage_worker(stop_event):
    global _WORKER
    directory = store.DATA / "managed-profiles"
    marker = directory / "enabled"
    child = None
    seen = None
    try:
        while not stop_event.is_set():
            try:
                stamp = marker.stat().st_mtime_ns
            except FileNotFoundError:
                stamp = None
            if child and child.poll() is not None:
                child = None
                _WORKER = None
                with store.db(True) as db:
                    store.message(db, ROOM_ID, "system", "协作台",
                                  "本地 Codex 的接单进程已停止。请查看本机数据目录中的 codex-worker.log，"
                                  "再双击「打开协作台Codex群.command」并重新发送需要处理的问题。")
            if stamp is not None and stamp != seen and child is None:
                with (store.DATA / "codex-worker.log").open("ab") as log:
                    child = subprocess.Popen([sys.executable, str(ROOT / "codex_worker.py"),
                                              "--profile", PROFILE, "--watch"], cwd=ROOT,
                                             env=worker_env(directory), stdin=subprocess.DEVNULL,
                                             stdout=log, stderr=log, start_new_session=True)
                    _WORKER = child
            seen = stamp
            stop_event.wait(0.5)
    finally:
        if child:
            stop_child(child)
        _WORKER = None
