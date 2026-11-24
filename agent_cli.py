#!/usr/bin/env python3
"""Local agent client and a newline-delimited MCP stdio adapter (Python 3.9+)."""
import argparse
import getpass
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from urllib.parse import urlsplit

DEFAULT_SERVER = "http://127.0.0.1:18473"
VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class ClientError(Exception):
    pass


def identifier(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ClientError("ID 应为字母、数字、下划线或连字符。")
    value = str(value)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ClientError("ID 应为 1 至 100 个字母、数字、下划线或连字符。")
    return value


def address(server):
    url = urlsplit(server)
    if (url.scheme != "http" or url.hostname not in ("127.0.0.1", "localhost", "::1")
            or url.username is not None or url.password is not None or url.path not in ("", "/")
            or url.query or url.fragment):
        raise ClientError("服务地址须为本机回环 HTTP 地址。")
    # Even for localhost use a literal loopback IP, without a DNS or proxy lookup.
    return ("::1" if url.hostname == "::1" else "127.0.0.1", url.port or 80)


def request(config, path, data=None):
    host, port = address(config["server"])
    connection = http.client.HTTPConnection(host, port, timeout=15)
    headers = {"Accept": "application/json", "Authorization": "Bearer " + config["token"]}
    body = None
    if data is not None:
        body = json.dumps(data, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json"
    try:
        connection.request("GET" if body is None else "POST", "/api/agent/" + path, body, headers)
        response = connection.getresponse()
        raw = response.read()
        if not 200 <= response.status < 300:
            message = response.reason
            try:
                info = json.loads(raw)
                if isinstance(info, dict):
                    message = str(info.get("error") or info.get("message") or message)[:300]
            except (ValueError, UnicodeError):
                pass
            raise ClientError("HTTP {}：{}".format(response.status, message))
        return json.loads(raw)
    except (OSError, http.client.HTTPException) as exc:
        raise ClientError("无法连接本地工作台，请检查服务及端口。{}".format(exc)) from exc
    finally:
        connection.close()


def profile_file(profile):
    name = identifier(profile)
    root = os.getenv("PACHAT_LOCAL_PROFILE_DIR")
    directory = Path(root).expanduser() if root else Path.home() / ".pachat-local" / "profiles"
    return directory / (name + ".json")


def save_profile(path, config):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".login-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(config, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load_profile(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise ClientError("找不到这个 profile，请先运行 login。") from None
    with os.fdopen(fd, encoding="utf-8") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ClientError("profile 应由当前用户拥有，且只有本人可读写（chmod 600）。")
        config = json.load(stream)
    if not isinstance(config, dict) or not isinstance(config.get("server"), str) or not isinstance(config.get("token"), str):
        raise ClientError("profile 已损坏，请重新运行 login。")
    return config


def run_action(config, command, item=None, content=None):
    if command == "rooms":
        return {"rooms": request(config, "me")["rooms"]}
    if command == "inbox":
        return request(config, "inbox")
    item = identifier(item)
    if command == "read":
        return request(config, "rooms/" + item)
    if not isinstance(content, str) or not content.strip():
        raise ClientError("请输入要发送的正文。")
    path = ("jobs/" + item + "/reply") if command == "reply" else ("rooms/" + item + "/messages")
    return request(config, path, {"text": content})


def schema(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def id_schema(label):
    return {"description": label, "type": ["string", "integer"]}


TOOLS = [
    {"name": "inbox", "description": "读取分配给当前 Agent 的待办及对应群聊上下文。", "inputSchema": schema({})},
    {"name": "read", "description": "读取当前 Agent 已加入的一个群及其消息。", "inputSchema": schema({"room_id": id_schema("群 ID")})},
    {"name": "reply", "description": "在群中提交一个指定待办的回答，使用相同正文可以重试。", "inputSchema": schema({"job_id": id_schema("待办 ID"), "text": {"type": "string"}})},
]


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def mcp_method(config, method, params):
    if method == "initialize":
        version = params.get("protocolVersion")
        return {"protocolVersion": version if version in VERSIONS else VERSIONS[0],
                "capabilities": {"tools": {}}, "serverInfo": {"name": "pachat-local", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method != "tools/call":
        raise RpcError(-32601, "Method not found")
    name = params.get("name")
    args = params.get("arguments", {})
    spec = next((t["inputSchema"] for t in TOOLS if t["name"] == name), None)
    if spec is None or not isinstance(args, dict) or set(args) != set(spec["required"]):
        raise RpcError(-32602, "Unknown tool or invalid arguments")
    try:
        result = run_action(config, name, args.get("job_id", args.get("room_id")), args.get("text"))
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]}
    except (ClientError, ValueError, KeyError) as exc:
        return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}


def run_mcp(config):
    for line in sys.stdin.buffer:
        rpc_id = None
        try:
            try:
                msg = json.loads(line)
            except (ValueError, UnicodeError):
                raise RpcError(-32700, "Parse error") from None
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise RpcError(-32600, "Invalid Request")
            if "method" not in msg and ("result" in msg or "error" in msg):
                continue
            if not isinstance(msg.get("method"), str):
                raise RpcError(-32600, "Invalid Request")
            if "id" not in msg:  # No response to initialized/cancelled notifications.
                continue
            rpc_id = msg["id"]
            params = msg.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "Invalid params")
            response = {"result": mcp_method(config, msg["method"], params)}
        except RpcError as exc:
            response = {"error": {"code": exc.code, "message": str(exc)}}
        response.update({"jsonrpc": "2.0", "id": rpc_id})
        print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description="本地 Agent 群聊客户端")
    cmds = parser.add_subparsers(dest="command", required=True)
    for name in ("login", "inbox", "rooms", "read", "reply", "post", "mcp"):
        sub = cmds.add_parser(name)
        sub.add_argument("--profile", required=True, help="此 Agent 的本地 profile 名称")
        if name == "login":
            sub.add_argument("--server", default=DEFAULT_SERVER, help="本机服务地址")
        if name in ("read", "post", "reply"):
            sub.add_argument("item", metavar="JOB_ID" if name == "reply" else "ROOM_ID")
        if name in ("post", "reply"):
            sub.add_argument("--text", required=True)
    args = parser.parse_args()
    path = profile_file(args.profile)
    if args.command == "login":
        address(args.server)
        if not sys.stdin.isatty():
            raise ClientError("请在交互终端运行 login，以便隐藏令牌输入。")
        config = {"server": args.server, "token": getpass.getpass("粘贴本机工作台签发的 Agent 令牌：").strip()}
        if not config["token"]:
            raise ClientError("未输入令牌。")
        me = request(config, "me")
        agent = me["agent"]
        if not agent.get("id") or not isinstance(agent.get("name"), str) or not isinstance(me.get("rooms"), list):
            raise ClientError("服务返回的 Agent 身份格式错误，未保存凭据。")
        save_profile(path, config)
        print("已连接 {}，profile 为 {}。".format(agent["name"], args.profile))
        return
    config = load_profile(path)
    if args.command == "mcp":
        run_mcp(config)
        return
    result = run_action(config, args.command, getattr(args, "item", None), getattr(args, "text", None))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, ValueError, KeyError, EOFError) as error:
        sys.exit(str(error))
