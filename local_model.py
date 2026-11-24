#!/usr/bin/env python3
"""Download, verify and manage the bundled local inference configuration on macOS."""

import argparse
import fcntl
import hashlib
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from model_backend import DEFAULT_MODEL, DEFAULT_URL, KEY_FILE, MODEL_HOME, ModelBackend


FILENAME = DEFAULT_MODEL + ".gguf"
WEIGHTS = MODEL_HOME / "models" / FILENAME
EXPECTED_SIZE = 2497280256
EXPECTED_SHA = "7485fe6f11af29433bc51cab58009521f205840f5b4ae3a32fa7f92e8534fdf5"
URL = ("https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/"
       "bc640142c66e1fdd12af0bd68f40445458f3869b/" + FILENAME + "?download=true")
PID = MODEL_HOME / "model.pid"
LOG = MODEL_HOME / "model.log"


def verify(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == EXPECTED_SHA


def install():
    if WEIGHTS.is_file() and verify(WEIGHTS):
        print(f"已核验已有权重：{WEIGHTS}")
        return
    WEIGHTS.parent.mkdir(exist_ok=True)
    partial = WEIGHTS.with_suffix(".gguf.part")
    print("正在下载官方 Qwen3-4B（2.50 GB），完成后校验 SHA-256。", flush=True)
    if not partial.is_file() or partial.stat().st_size < EXPECTED_SIZE:
        subprocess.run(["curl", "--fail", "--location", "--show-error", "--retry", "2",
                        "--continue-at", "-", "--output", str(partial), URL], check=True)
    if not verify(partial):
        partial.unlink()
        raise RuntimeError("权重校验失败，已移除未通过的下载。请重新运行 install。")
    os.replace(partial, WEIGHTS)
    print(f"模型已安装：{WEIGHTS}")


def owned_pid():
    try:
        pid = int(PID.read_text().strip())
        if pid < 2:
            return None
        process = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                                 capture_output=True, text=True)
        line = process.stdout.strip()
        # Reject a stale PID that was reused for another model or application.
        if (line.startswith("/") and "llama-server -m " + str(WEIGHTS) + " " in line
                and "--api-key-file " + str(KEY_FILE) in line):
            return pid
    except (OSError, ValueError):
        pass
    return None


def ready(backend):
    return backend.status()["model_status"] == "ready"


def start(backend):
    if ready(backend):
        print(f"本机模型已就绪：{backend.model}")
        return
    if backend.url != DEFAULT_URL or backend.model != DEFAULT_MODEL:
        raise RuntimeError("指定的自定义本机模型不可用，请先启动对应模型服务。")
    if not WEIGHTS.is_file() or WEIGHTS.stat().st_size != EXPECTED_SIZE:
        raise RuntimeError("请运行 python3 local_model.py install 安装本地模型。")
    binary = shutil.which("llama-server")
    if not binary:
        for candidate in ["/opt/homebrew/bin/llama-server", "/usr/local/bin/llama-server"]:
            if Path(candidate).is_file():
                binary = candidate
                break
    if not binary:
        raise RuntimeError("未发现 llama-server。可用 brew install llama.cpp 安装。")
    if not owned_pid():
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", 18765)) == 0:
                raise RuntimeError("本机 18765 端口已有其他服务在运行。")
        if not KEY_FILE.is_file():
            key = secrets.token_urlsafe(32)
            with KEY_FILE.open("x") as output:
                output.write(key + "\n")
        command = [binary, "-m", str(WEIGHTS), "--host", "127.0.0.1", "--port", "18765",
                   "--alias", DEFAULT_MODEL, "--ctx-size", "8192", "--parallel", "1",
                   "--reasoning", "off", "--offline", "--no-webui", "--api-key-file", str(KEY_FILE)]
        # Don't inherit flags or proxies from unrelated llama.cpp installations.
        env = {k: v for k, v in os.environ.items() if not k.startswith("LLAMA_ARG_")}
        with LOG.open("w") as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     start_new_session=True, env=env)
            PID.write_text(str(child.pid))
    for _ in range(80):
        if ready(backend):
            print(f"本机模型已就绪：{backend.model}")
            return
        if not owned_pid():
            break
        time.sleep(0.5)
    raise RuntimeError(f"模型尚未就绪，请查看 {LOG}")


def stop():
    pid = owned_pid()
    if not pid:
        print("本项目启动的模型已停止。")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not owned_pid():
            PID.unlink(missing_ok=True)
            print("已停止本项目启动的本机模型。")
            return
        time.sleep(0.1)
    raise RuntimeError("停止超时，请检查模型进程。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["install", "start", "status", "stop"])
    args = parser.parse_args()
    os.umask(0o077)
    MODEL_HOME.mkdir(parents=True, exist_ok=True)
    backend = ModelBackend()
    if args.command == "status":
        print(f"本机模型{'已就绪' if ready(backend) else '未就绪'}：{backend.model}")
        return
    with (MODEL_HOME / "control.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if args.command == "install":
            install()
        elif args.command == "start":
            start(backend)
        else:
            stop()


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
