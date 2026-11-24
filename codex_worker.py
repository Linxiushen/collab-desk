#!/usr/bin/env python3
"""Reply to assigned room tasks through a real Codex CLI using the on-device Qwen model."""
import argparse
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time

from agent_cli import ClientError, identifier, load_profile, profile_file, request, run_action
from model_backend import DEFAULT_MODEL, DEFAULT_URL, KEY_FILE, ModelBackend


def question_for(agent, job):
    records = []
    remaining = 4000
    for message in reversed(job["history"]):
        if message["id"] == job["root"]:
            continue
        text = message["text"][:1000]
        if len(text) > remaining:
            break
        remaining -= len(text)
        records.append({"author": message["author"], "text": text})
    return json.dumps({"your_name": agent["name"], "your_role": agent["role"][:500],
                       "conversation": list(reversed(records)), "question": job["prompt"][:3000]},
                      ensure_ascii=False)


@contextmanager
def model_bridge():
    """Give Codex one text-only model request to the fixed loopback llama.cpp server."""
    token = secrets.token_urlsafe(24)
    class Bridge(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/v1/responses" or self.headers.get("Authorization") != "Bearer " + token:
                self.send_error(403)
                return
            if self.server.used:
                self.send_error(429)
                return
            self.server.used = True
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2_000_000:
                    raise ValueError("invalid request size")
                incoming = json.loads(self.rfile.read(length))
                # Codex also includes tool descriptions and environment metadata. Give the local
                # model only the user's last supplied worksheet plus the bridge's fixed instructions.
                item = next(entry for entry in reversed(incoming["input"]) if entry.get("role") == "user")
                utterance = "\n".join(x["text"] for x in item["content"] if x.get("type") == "input_text")
                if not utterance:
                    raise ValueError("missing text")
                payload = {"model": DEFAULT_MODEL, "instructions": GUIDANCE, "input": utterance,
                           "stream": True, "temperature": 0.2, "max_output_tokens": 480}
                conn = http.client.HTTPConnection("127.0.0.1", 18765, timeout=55)
                try:
                    conn.request("POST", "/v1/responses", json.dumps(payload, ensure_ascii=False).encode(),
                                 {"Content-Type": "application/json", "Authorization": "Bearer " + KEY_FILE.read_text().strip()})
                    res = conn.getresponse()
                    body = res.read(1_000_001)
                    if res.status != 200 or len(body) > 1_000_000 or b"event: response.completed" not in body:
                        raise ValueError("incomplete local response")
                    for line in body.splitlines():
                        if line.startswith(b"data: {"):
                            event = json.loads(line[len(b"data: "):])
                            if event.get("item", {}).get("type", "message") not in ("message", "reasoning"):
                                raise ValueError("only text answers are supported")
                finally:
                    conn.close()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (OSError, ValueError, KeyError, TypeError, StopIteration, http.client.HTTPException):
                self.send_error(502)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Bridge)
    server.used = False
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, token
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


GUIDANCE = ("你是已加入本机讨论室的一名成员，按照你的职责，直接回答当前用户问题。"
            "只使用给出的资料；如果需要实时资料而群里尚未提供，就说明缺什么。用用户的语言简明回答。")


def session_config(port, instructions):
    settings = {
        "model_provider": "tongzhuo", "model_providers.tongzhuo.name": "Collab Desk localhost",
        "model_providers.tongzhuo.base_url": "http://127.0.0.1:{}/v1".format(port), "model_providers.tongzhuo.wire_api": "responses",
        "model_providers.tongzhuo.env_key": "TONGZHUO_LOCAL_KEY",
        "model_providers.tongzhuo.request_max_retries": 0, "model_providers.tongzhuo.stream_max_retries": 0,
        "model_providers.tongzhuo.supports_websockets": False, "model_instructions_file": str(instructions),
        "model_reasoning_effort": "low", "web_search": "disabled", "analytics.enabled": False,
    }
    for name in ("shell_tool", "apps", "plugins", "hooks", "multi_agent", "browser_use", "computer_use", "image_generation"):
        settings["features." + name] = False
    result = []
    for name, value in settings.items():
        result.extend(["-c", name + "=" + json.dumps(value, ensure_ascii=False)])
    return result


def codex_binary():
    candidate = shutil.which("codex")
    if candidate:
        return candidate
    bundled = "Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
    for apps in (Path.home() / "Desktop", Path("/Applications"), Path.home() / "Applications"):
        for application, relative in (("ChatGPT.app", bundled), ("Codex.app", bundled), ("Codex.app", "Contents/Resources/codex")):
            candidate = apps / application / relative
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
    return None


def codex_reply(agent, job):
    binary = codex_binary()
    confinement = "/usr/bin/sandbox-exec"
    if not binary or not Path(confinement).is_file():
        raise ClientError("需要 macOS、Codex CLI 和 sandbox-exec。")
    if not KEY_FILE.is_file():
        raise ClientError("请先启动协作台附带的本机模型。")
    with tempfile.TemporaryDirectory(prefix="tongzhuo-codex-") as temporary, model_bridge() as (port, token):
        root = Path(temporary).resolve()
        work = root / "work"
        home = root / "config"
        work.mkdir()
        home.mkdir()
        instructions = root / "instructions"
        instructions.write_text(GUIDANCE)
        output = root / "answer"
        # The child receives only a one-use local model bridge token.
        env = {"PATH": "/usr/bin:/bin:/opt/homebrew/bin", "HOME": str(root), "CODEX_HOME": str(home),
               "TMPDIR": str(root), "LANG": "en_US.UTF-8", "TONGZHUO_LOCAL_KEY": token}
        # Codex itself may be installed inside the user's home. Permit reads inside the executable's own
        # directory, but never grant the child blanket access to the user's profile, docs or group database.
        appdir = Path(binary).resolve().parent
        sandbox = (
            '(version 1) (allow default) (deny network-outbound) '
            '(allow network-outbound (remote ip "localhost:' + str(port) + '")) '
            '(deny file-write*) (allow file-write* (subpath ' + json.dumps(str(root)) + ') (literal "/dev/null")) '
            '(deny file-read* (subpath ' + json.dumps(str(Path.home().resolve())) + ')) '
            '(allow file-read* (subpath ' + json.dumps(str(appdir)) + ') (subpath ' + json.dumps(str(root)) + '))'
        )
        cmd = [confinement, "-p", sandbox, binary, "--no-daemon", "-a", "never", "exec", "--ephemeral",
               "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check", "-s", "read-only",
               "-C", str(work), "-m", DEFAULT_MODEL, "--color", "never", "-o", str(output)]
        cmd += session_config(port, instructions) + ["-"]
        proc = subprocess.Popen(cmd, env=env, cwd=work, text=True, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            proc.communicate(input=question_for(agent, job), timeout=75)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            raise
        if proc.returncode or not output.exists():
            raise ClientError("本机 Codex 未能生成回答。请检查 codex --version 与 python3 local_model.py status。")
        answer = output.read_text().strip()
        if not answer or len(answer) > 16000:
            raise ClientError("Codex 返回空消息或超长文本。")
        return answer


def local_model_ready():
    backend = ModelBackend()
    # Codex's bridge always uses the installed llama.cpp service.
    backend.url, backend.model = DEFAULT_URL, DEFAULT_MODEL
    return backend.status()["model_status"] == "ready"


def report_failure(config, jid, detail):
    text = f"任务 {jid}：{detail}。请重发问题或手动重试。"
    request(config, f"jobs/{jid}/fail", {"text": text})
    print(text, flush=True)


def run_once(config, agent, journal, retry_id=None):
    if retry_id is not None:
        # Reopen the one task authorized by the operator. Keep any completed draft.
        response = request(config, f"jobs/{retry_id}/retry", {})
        journal.execute("DELETE FROM attempts WHERE id=? AND draft IS NULL", (retry_id,))
        journal.commit()
        jobs = [response["job"]] if response.get("job") else run_action(config, "inbox")["jobs"]
    else:
        jobs = run_action(config, "inbox")["jobs"]
    worked = False
    ready = None
    for job in jobs:
        jid = identifier(job["id"])
        if retry_id is not None and jid != retry_id:
            continue
        previous = journal.execute("SELECT draft FROM attempts WHERE id=?", (jid,)).fetchone()
        if previous and previous[0] is None:
            report_failure(config, jid, "上次回复中断")
            worked = True
            continue
        if previous and previous[0] is not None:
            answer = previous[0]
        else:
            if ready is None:
                ready = local_model_ready()
            if not ready:
                continue
            journal.execute("INSERT OR IGNORE INTO attempts(id) VALUES(?)", (jid,))
            journal.commit()
            print("本机 Codex 正在处理任务 {}。".format(jid), flush=True)
            try:
                answer = codex_reply(agent, job)
            except KeyboardInterrupt:
                try:
                    report_failure(config, jid, "本次回复已中断")
                except (ClientError, OSError):
                    pass  # If the server has stopped, the next worker reports the interruption.
                raise
            except (ClientError, subprocess.TimeoutExpired, OSError) as error:
                detail = "本机 Codex 生成超时" if isinstance(error, subprocess.TimeoutExpired) else "本机 Codex 生成失败"
                report_failure(config, jid, detail)
                print(error, file=sys.stderr, flush=True)
                worked = True
                continue
            # Store before POST so a failed or interrupted submission can be retried verbatim.
            journal.execute("UPDATE attempts SET draft=? WHERE id=?", (answer, jid))
            journal.commit()
        result = run_action(config, "reply", jid, answer)
        journal.execute("DELETE FROM attempts WHERE id=?", (jid,))
        journal.commit()
        print("已提交任务 {}，群消息编号 {}。".format(jid, result["id"]), flush=True)
        return True
    return worked


def main():
    parser = argparse.ArgumentParser(description="通过本机 Codex CLI 和 Qwen 回复分配给这个 Agent 的任务")
    parser.add_argument("--profile", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--watch", action="store_true", help="持续检查收件箱，Ctrl-C 停止")
    mode.add_argument("--retry", metavar="JOB_ID", help="手动重试曾中断的待办")
    args = parser.parse_args()
    os.umask(0o077)
    path = profile_file(args.profile)
    config = load_profile(path)
    agent = request(config, "me")["agent"]
    retry_id = identifier(args.retry) if args.retry else None
    # Independent profile names for the same token share one lock and one reply journal.
    key = hashlib.sha256((config["server"] + "|" + config["token"]).encode()).hexdigest()[:32]
    with (path.parent / (".worker-" + key + ".lock")).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ClientError("此 Agent 已有一个本机 Codex 回复进程。") from None
        journal = sqlite3.connect(path.parent / (".worker-" + key + ".sqlite3"))
        try:
            journal.execute("CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,draft TEXT)")
            print("已连接 {}，使用本机 Codex CLI 和 {}。".format(agent["name"], DEFAULT_MODEL), flush=True)
            while True:
                worked = run_once(config, agent, journal, retry_id)
                if not args.watch:
                    if not worked:
                        print("当前没有可处理的指定任务，或本机模型尚未就绪。")
                    return
                time.sleep(2 if worked else 5)
        finally:
            journal.close()


if __name__ == "__main__":
    def stop_worker(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_worker)
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ClientError, OSError, sqlite3.Error, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        sys.exit(str(error))
