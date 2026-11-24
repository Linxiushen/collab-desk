#!/usr/bin/env python3
"""Single-user local group chat. Runs on IPv4 loopback only."""
import json
import os
import re
import sqlite3
import sys
import threading
import traceback
import uuid
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import store as s
from model_backend import ModelBackend
from managed_codex import manage_worker, worker_status, AGENT_ID as CODEX_AGENT_ID

STATIC = Path(__file__).resolve().parent / "static"
ENGINE = ModelBackend()
PULSE = threading.Event()
STOP = threading.Event()
LOCKED = """<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>打开协作台</title>
<style>*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7fa;color:#202634;font:16px/1.8 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{max-width:480px;margin:24px;padding:44px;border:1px solid #e3e7ee;background:#fff;border-radius:24px;box-shadow:0 20px 70px #1f2c4510}
.brand{display:inline-block;background:#4e66e8;color:white;border-radius:13px;padding:8px 16px;font-weight:650}
h1{margin:28px 0 12px;font-size:28px;letter-spacing:-.7px}p{color:#647084}b{color:#253349}small{display:block;border-top:1px solid #edf0f5;padding-top:18px;margin-top:28px;color:#8a94a4}</style>
<main><span class="brand">协作台 / Collab Desk</span><h1>你的 Agent，正在协作台里。</h1>
<p>双击项目里的 <b>启动协作台.command</b>，即可打开你的本地工作区。</p><small>已有讨论记录会自动保留。换了浏览器时，重新打开启动器即可。</small></main></html>"""


def infer():
    while not STOP.is_set():
        try:
            with s.db() as c:
                queued = c.execute("""SELECT 1 FROM jobs j JOIN agents a ON a.id=j.agent_id
                    WHERE j.state='pending' AND a.kind='local' LIMIT 1""").fetchone()
            if queued and ENGINE.status()["model_status"] != "ready":
                STOP.wait(1.5)
                continue
            with s.db(True) as c:
                job = c.execute("""SELECT j.* FROM jobs j JOIN agents a ON a.id=j.agent_id
                    WHERE j.state='pending' AND a.kind='local' ORDER BY j.id LIMIT 1""").fetchone()
                if job:
                    c.execute("UPDATE jobs SET state='working' WHERE id=?", (job["id"],))
                    who = dict(c.execute("SELECT id,name,role,kind FROM agents WHERE id=?", (job["agent_id"],)).fetchone())
                    history = s.context(c, job)
            if not job:
                PULSE.wait(1.5)
                PULSE.clear()
                continue
            try:
                reply = ENGINE.reply(who, history)
                if not isinstance(reply, str) or not reply.strip():
                    raise ValueError("本地模型返回了空消息")
                has_error = False
            except Exception as exc:
                reply = "本地模型未能完成回复。%s" % (str(exc)[:400] or "请查看模型状态后重发。")
                has_error = True
            with s.db(True) as c:
                try:
                    s.finish(c, job["id"], job["agent_id"], reply, error=has_error)
                except s.Problem:
                    pass  # Owner may have removed a member while the model was running.
        except (sqlite3.Error, OSError):
            traceback.print_exc()
            STOP.wait(1)


def state():
    model = ENGINE.status()
    with s.db() as c:
        rooms = s.rows(c, """SELECT r.*,
                    (SELECT count(*) FROM messages WHERE room_id=r.id) message_count,
                    (SELECT max(id) FROM messages WHERE room_id=r.id) last_message_id
                    FROM rooms r ORDER BY r.rowid""")
        for room in rooms:
            latest = c.execute("SELECT author,text,created_at FROM messages WHERE id=?",
                               (room.pop("last_message_id"),)).fetchone()
            room["last_message"] = dict(latest) if latest else None
        return dict(rooms=rooms, agents=s.rows(c, "SELECT id,name,kind,role FROM agents ORDER BY rowid"), **model)


def owner_request(path, post, body):
    if path == "/api/state" and not post:
        return state()
    if post and path == "/api/control/stop":
        threading.Thread(target=SERVER.shutdown, daemon=True).start()
        return {"ok": True}
    with s.db(post) as c:
        if post and path == "/api/agents":
            return s.add_agent(c, body)
        if post and path == "/api/rooms":
            rid = uuid.uuid4().hex[:16]
            c.execute("INSERT INTO rooms VALUES(?,?)", (rid, s.clean(body.get("name"), "讨论室名称", 60)))
            s.put_members(c, rid, body.get("agent_ids", ["analyst", "reviewer"]))
            return {"id": rid}
        match = re.fullmatch(r"/api/rooms/([a-zA-Z0-9_-]+)(?:/(messages|members))?", path)
        if match:
            rid, action = match.groups()
            room = s.room(c, rid)
            if not post and not action:
                counts = dict(c.execute("""SELECT a.kind,count(*) FROM jobs j JOIN agents a ON a.id=j.agent_id WHERE
                    j.room_id=? AND j.state IN ('pending','working') GROUP BY a.kind""", (rid,)).fetchall())
                members = s.members(c, rid)
                activity = {}
                for member in members:
                    aid = member["id"]
                    queue = dict(c.execute("""SELECT state,count(*) FROM jobs WHERE room_id=? AND agent_id=?
                        AND state IN ('pending','working') GROUP BY state""", (rid, aid)).fetchall())
                    last = c.execute("""SELECT max(created_at) FROM messages
                        WHERE room_id=? AND agent_id=? AND kind='agent'""", (rid, aid)).fetchone()[0]
                    activity[aid] = dict(pending=queue.get("pending", 0), working=queue.get("working", 0),
                        last_reply_at=last, worker_status=worker_status() if aid == CODEX_AGENT_ID else None)
                return dict(room=room, members=members, messages=s.room_messages(c, rid), member_activity=activity,
                            busy=bool(counts.get("local")), waiting_external=counts.get("external", 0))
            if post and action == "members":
                s.put_members(c, rid, body.get("agent_ids"))
                return {"ok": True}
            if post and action == "messages":
                result = s.send(c, rid, body)
                PULSE.set()
                return result
    raise s.Problem(404, "没有这个接口")


def agent_request(path, auth, post, body):
    if not auth.startswith("Bearer ") or len(auth) > 256:
        raise s.Problem(401, "需要 Agent 令牌")
    with s.db(post) as c:
        agent = c.execute("SELECT id,name,kind,role FROM agents WHERE token_hash=? AND kind='external'",
                          (s.digest(auth[7:]),)).fetchone()
        if agent is None:
            raise s.Problem(401, "Agent 令牌无效")
        aid = agent["id"]
        if not post and path == "/api/agent/me":
            return {"agent": dict(agent), "rooms": s.rows(c, """SELECT r.id,r.name FROM rooms r
                JOIN members m ON r.id=m.room_id WHERE m.agent_id=? ORDER BY r.rowid""", (aid,))}
        if not post and path == "/api/agent/inbox":
            return s.inbox(c, aid)
        match = re.fullmatch(r"/api/agent/jobs/(\d{1,18})/(reply|fail|retry)", path)
        if post and match:
            jid, action = int(match[1]), match[2]
            if action == "retry":
                job = c.execute("SELECT * FROM jobs WHERE id=? AND agent_id=?", (jid, aid)).fetchone()
                if job is None:
                    raise s.Problem(404, "没有这条任务")
                s.check_member(c, job["room_id"], aid)
                if job["state"] not in ("failed", "pending"):
                    raise s.Problem(409, "只有失败的任务可以重试")
                c.execute("UPDATE jobs SET state='pending',response=NULL WHERE id=?", (jid,))
                detail = dict(c.execute("""SELECT j.id,j.room_id,j.root,r.name room_name,p.text prompt
                    FROM jobs j JOIN rooms r ON r.id=j.room_id JOIN messages p ON p.id=j.root
                    WHERE j.id=?""", (jid,)).fetchone())
                detail["history"] = s.context(c, detail)
                return {"ok": True, "job": detail}
            return s.finish(c, jid, aid, s.clean(body.get("text"), "回复", 16000),
                            external=True, error=(action == "fail"))
        match = re.fullmatch(r"/api/agent/rooms/([a-zA-Z0-9_-]+)(/messages)?", path)
        if match:
            rid, action = match.groups()
            s.check_member(c, rid, aid)
            if not post and not action:
                return {"room": s.room(c, rid), "messages": s.room_messages(c, rid)}
            if post and action:
                mid = s.message(c, rid, "agent", agent["name"], s.clean(body.get("text"), "消息", 16000), aid)
                return {"ok": True, "id": mid}
    raise s.Problem(404, "没有这个接口")


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentRoom/1"

    def log_message(self, *args):
        pass  # Do not log access tickets, chat text or tokens.

    def send_bytes(self, status, value, mime, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(value)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; script-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        for key, val in (headers or {}).items():
            self.send_header(key, val)
        self.end_headers()
        self.wfile.write(value)

    def json(self, status, value):
        return self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def owner(self):
        jar = cookies.SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        token = jar.get("tongzhuo_owner")
        if token:
            with s.db() as c:
                return s.valid_key(c, token.value, "session")
        return False

    def body(self):
        if self.headers.get_content_type() != "application/json":
            raise s.Problem(415, "请使用 JSON")
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if not 0 < n <= 80000:
                raise s.Problem(413, "请求大小不正确")
            value = json.loads(self.rfile.read(n))
        except (ValueError, UnicodeError, RecursionError):
            raise s.Problem(400, "无效的 JSON")
        if not isinstance(value, dict):
            raise s.Problem(400, "需要 JSON 对象")
        return value

    def route(self, post):
        host = self.headers.get("Host", "")
        if host != "127.0.0.1:%d" % s.PORT:
            raise s.Problem(403, "仅接受本机地址 127.0.0.1")
        if post and self.headers.get("Origin") not in (None, "http://" + host):
            raise s.Problem(403, "请求来源不匹配")
        url = urlsplit(self.path)
        path = url.path
        if path == "/health" and not post:
            return self.json(200, {"service": "tongzhuo-local", "version": 1})
        if path == "/open" and not post:
            ticket = parse_qs(url.query).get("t", [""])[0]
            with s.db(True) as c:
                if not s.valid_key(c, ticket, "ticket"):
                    return self.send_bytes(403, LOCKED.encode(), "text/html; charset=utf-8")
                c.execute("DELETE FROM browser_keys WHERE hash=?", (s.digest(ticket),))
                session = s.browser_key(c, "session", 30 * 86400)
                requested = parse_qs(url.query).get("room", [""])[0]
                target = "/?room=" + requested if (
                    re.fullmatch("[a-zA-Z0-9_-]{1,80}", requested)
                    and c.execute("SELECT 1 FROM rooms WHERE id=?", (requested,)).fetchone()) else "/"
            return self.send_bytes(303, b"", "text/plain", {"Location": target,
                "Set-Cookie": "tongzhuo_owner=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" % session})
        if path.startswith("/api/agent/"):
            result = agent_request(path, self.headers.get("Authorization", ""), post, self.body() if post else {})
            return self.json(200, result)
        if path.startswith("/api/"):
            if not self.owner():
                raise s.Problem(401, "请双击「启动协作台.command」打开工作台")
            result = owner_request(path, post, self.body() if post else {})
            return self.json(200, result)
        if not post and path == "/" and not self.owner():
            return self.send_bytes(200, LOCKED.encode(), "text/html; charset=utf-8")
        assets = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "application/javascript"),
                  "/style.css": ("style.css", "text/css")}
        asset = assets.get(path.removeprefix("/static") if path.startswith("/static/") else path)
        if asset and not post:
            return self.send_bytes(200, (STATIC / asset[0]).read_bytes(), asset[1] + "; charset=utf-8")
        raise s.Problem(404, "未找到")

    def run(self, post):
        try:
            self.route(post)
        except s.Problem as e:
            self.json(e.status, {"error": e.message})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self.json(500, {"error": "本机服务发生异常，请查看服务日志。"})

    def do_GET(self):
        self.run(False)

    def do_POST(self):
        self.run(True)


if __name__ == "__main__":
    os.umask(0o077)
    SERVER = ThreadingHTTPServer(("127.0.0.1", s.PORT), Handler)
    s.init()
    threading.Thread(target=infer, name="inference", daemon=True).start()
    codex_supervisor = threading.Thread(target=manage_worker, args=(STOP,), name="codex-local", daemon=True)
    codex_supervisor.start()
    try:
        SERVER.serve_forever()
    finally:
        STOP.set()
        PULSE.set()
        codex_supervisor.join(timeout=8)
        SERVER.server_close()
