#!/usr/bin/env python3
"""Start the local services and open an authenticated browser tab."""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import store

ROOT = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:%d" % store.PORT
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def is_ready():
    try:
        with HTTP.open(BASE + "/health", timeout=1) as response:
            return json.load(response).get("service") == "tongzhuo-local"
    except (OSError, ValueError, AttributeError):
        return False


def start():
    if not is_ready():
        with (store.DATA / "server.log").open("ab") as log:
            proc = subprocess.Popen([sys.executable, str(ROOT / "server.py")], cwd=ROOT, stdin=subprocess.DEVNULL,
                                    stdout=log, stderr=log, start_new_session=True)
        for _ in range(80):
            if is_ready():
                break
            if proc.poll() is not None:
                raise RuntimeError("服务未能启动，请检查端口是否被占用及日志 " + str(store.DATA / "server.log"))
            time.sleep(0.1)
        else:
            raise RuntimeError("服务启动超时，请查看 " + str(store.DATA / "server.log"))
    # If a provider isn't ready, the interface and external-agent inbox still work.
    result = subprocess.run([sys.executable, str(ROOT / "local_model.py"), "start"])
    if result.returncode:
        print("聊天工作区仍可使用。内置成员需等本地模型就绪后才能回答。", flush=True)


def stop():
    if is_ready():
        with store.db(True) as db:
            key = store.browser_key(db, "session", 15)
        req = urllib.request.Request(BASE + "/api/control/stop", data=b"{}",
                                     headers={"Content-Type": "application/json", "Cookie": "tongzhuo_owner=" + key})
        with HTTP.open(req, timeout=3) as reply:
            json.load(reply)
        for _ in range(35):
            if not is_ready():
                break
            time.sleep(0.1)
        if is_ready():
            raise RuntimeError("工作区尚未停止，请稍后再试。")
    subprocess.run([sys.executable, str(ROOT / "local_model.py"), "stop"], check=True)
    print("协作台已关闭。讨论记录保存在本机。")


def main():
    parser = argparse.ArgumentParser(description="启动协作台，本机 Agent 工作区")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--codex", action="store_true", help="打开使用本机模型的 Codex 工作群")
    parser.add_argument("--no-open", action="store_true", help="启动服务，不打开浏览器")
    args = parser.parse_args()
    os.umask(0o077)
    store.DATA.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (store.DATA / "launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.stop:
            stop()
            return
        start()
        room_query = ""
        if args.codex:
            from managed_codex import ensure_enabled
            room_query = "&room=" + ensure_enabled()
        if not args.no_open:
            with store.db(True) as db:
                ticket = store.browser_key(db, "ticket", 120)
            subprocess.run(["open", BASE + "/open?t=" + ticket + room_query], check=True)
        print("协作台已启动，工作区地址 " + BASE, flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
