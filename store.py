"""Durable rooms and addressed jobs. A write transaction owns each state transition."""
import contextlib
import hashlib
import os
import re
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

DATA = Path(os.environ.get("AGENT_ROOM_DATA", "~/Library/Application Support/TongZhuo")).expanduser()
PORT = int(os.environ.get("AGENT_ROOM_PORT", "18473"))
AGENT_FIELDS = "a.id,a.name,a.role,a.kind"
ROLES = [
    ("analyst", "分析员", "拆解问题，给出有依据的方案，区分已知和假设。", "local"),
    ("reviewer", "复核员", "独立复核分析员或群友的答案，指出具体差错，给出修正或说明依据一致。", "local"),
]


class Problem(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message
        super().__init__(message)


def clean(value, label, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise Problem(400, f"{label}须为 1 到 {limit} 个字符")
    return value.strip()


def digest(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


@contextlib.contextmanager
def db(write=False):
    conn = sqlite3.connect(DATA / "rooms.sqlite3", timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    with db(True) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS rooms(id TEXT PRIMARY KEY,name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS agents(id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE,role TEXT NOT NULL,kind TEXT NOT NULL,token_hash TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS members(room_id TEXT REFERENCES rooms,agent_id TEXT REFERENCES agents,PRIMARY KEY(room_id,agent_id));
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY AUTOINCREMENT,room_id TEXT REFERENCES rooms,author TEXT NOT NULL,kind TEXT NOT NULL,
          text TEXT NOT NULL,agent_id TEXT REFERENCES agents,root INTEGER,created_at TEXT NOT NULL DEFAULT(strftime('%Y-%m-%dT%H:%M:%fZ','now')));
        CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id,id);
        CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY AUTOINCREMENT,room_id TEXT REFERENCES rooms,agent_id TEXT REFERENCES agents,
          root INTEGER REFERENCES messages,state TEXT NOT NULL,response INTEGER REFERENCES messages,UNIQUE(root,agent_id));
        CREATE INDEX IF NOT EXISTS jobs_pending ON jobs(state,agent_id,id);
        CREATE TABLE IF NOT EXISTS browser_keys(hash TEXT PRIMARY KEY,kind TEXT,until INTEGER);
        """)
        c.execute("BEGIN IMMEDIATE")
        c.executemany("INSERT OR IGNORE INTO agents(id,name,role,kind) VALUES(?,?,?,?)", ROLES)
        if not c.execute("SELECT 1 FROM rooms LIMIT 1").fetchone():
            c.execute("INSERT INTO rooms VALUES('lobby','协作讨论室')")
            c.executemany("INSERT INTO members VALUES('lobby',?)", [(x[0],) for x in ROLES])
        c.execute("UPDATE jobs SET state='pending' WHERE state='working'")


def browser_key(c, kind, ttl):
    key = secrets.token_urlsafe(32)
    c.execute("DELETE FROM browser_keys WHERE until < ?", (int(time.time()),))
    c.execute("INSERT INTO browser_keys VALUES(?,?,?)", (digest(key), kind, int(time.time()) + ttl))
    return key


def valid_key(c, key, kind):
    return c.execute("SELECT 1 FROM browser_keys WHERE hash=? AND kind=? AND until>=?",
                     (digest(key), kind, int(time.time()))).fetchone() is not None


def rows(c, sql, args=()):
    return [dict(row) for row in c.execute(sql, args)]


def room(c, rid):
    r = c.execute("SELECT * FROM rooms WHERE id=?", (rid,)).fetchone()
    if r is None:
        raise Problem(404, "找不到这个讨论室")
    return dict(r)


def members(c, rid):
    return rows(c, f"SELECT {AGENT_FIELDS} FROM agents a JOIN members m ON a.id=m.agent_id WHERE m.room_id=? ORDER BY a.rowid", (rid,))


def room_messages(c, rid):
    return rows(c, "SELECT * FROM messages WHERE room_id=? ORDER BY id", (rid,))


def check_member(c, rid, aid):
    if not c.execute("SELECT 1 FROM members WHERE room_id=? AND agent_id=?", (rid, aid)).fetchone():
        raise Problem(403, "此 Agent 没有加入该讨论室")


def check_ids(value, allowed):
    if not isinstance(value, list) or not all(isinstance(v, str) and v in allowed for v in value):
        raise Problem(400, "成员列表包含无效的 Agent")
    return list(dict.fromkeys(value))


def put_members(c, rid, ids):
    room(c, rid)
    allowed = {r[0] for r in c.execute("SELECT id FROM agents")}
    chosen = check_ids(ids, allowed)
    c.execute("DELETE FROM members WHERE room_id=?", (rid,))
    c.executemany("INSERT INTO members VALUES(?,?)", [(rid, aid) for aid in chosen])
    c.execute("""UPDATE jobs SET state='cancelled' WHERE room_id=? AND state IN ('pending','working')
        AND agent_id NOT IN (SELECT agent_id FROM members WHERE room_id=?)""", (rid, rid))


def add_agent(c, body):
    name = clean(body.get("name"), "名称", 36)
    role = clean(body.get("role") or "独立接入的个人 Agent", "角色描述", 500)
    aid, token = "agent-" + uuid.uuid4().hex[:12], "tz_" + secrets.token_urlsafe(32)
    try:
        c.execute("INSERT INTO agents VALUES(?,?,?,'external',?)", (aid, name, role, digest(token)))
    except sqlite3.IntegrityError:
        raise Problem(409, "已存在同名 Agent")
    return {"id": aid, "token": token, "name": name}


def message(c, rid, kind, author, value, aid=None, root=None):
    return c.execute("INSERT INTO messages(room_id,kind,author,text,agent_id,root) VALUES(?,?,?,?,?,?)",
                     (rid, kind, author, value, aid, root)).lastrowid


def send(c, rid, body):
    room(c, rid)
    value = clean(body.get("text"), "消息", 12000)
    group = members(c, rid)
    all_ids = [a["id"] for a in group]
    if "targets" in body:
        chosen = check_ids(body["targets"], all_ids)
    else:
        chosen = [a["id"] for a in group if re.search(r"@" + re.escape(a["name"]) + r"(?=$|[\s，。,:：！？!?、])", value)]
        chosen = chosen or all_ids
    mid = message(c, rid, "human", "我", value)
    # Reviewers run after the analyst regardless of the order of recipient chips.
    c.executemany("INSERT INTO jobs(room_id,agent_id,root,state) VALUES(?,?,?,'pending')",
                  [(rid, aid, mid) for aid in all_ids if aid in chosen])
    return {"ok": True, "id": mid}


def context(c, job):
    # Group replies with their question even when a follow-up was posted first.
    # Reserve one slot for this question when its own replies fill the window.
    return rows(c, """
        SELECT * FROM (
            SELECT * FROM messages
            WHERE room_id=:room_id AND kind IN ('human','agent') AND COALESCE(root,id)<=:question
            ORDER BY (id=:question) DESC, COALESCE(root,id) DESC, id DESC
            LIMIT 60
        ) ORDER BY COALESCE(root,id), id
    """, {"room_id": job["room_id"], "question": job["root"]})


def inbox(c, aid):
    jobs = rows(c, """SELECT j.id,j.room_id,r.name room_name,j.root,p.text prompt FROM jobs j
        JOIN rooms r ON r.id=j.room_id JOIN messages p ON p.id=j.root JOIN members m USING(room_id,agent_id)
        WHERE j.agent_id=? AND j.state='pending' ORDER BY j.id LIMIT 20""", (aid,))
    for job in jobs:
        job["history"] = context(c, job)
    return {"jobs": jobs}


def finish(c, jid, aid, text, external=False, error=False):
    job = c.execute("SELECT * FROM jobs WHERE id=? AND agent_id=?", (jid, aid)).fetchone()
    if job is None:
        raise Problem(404, "没有这条任务")
    check_member(c, job["room_id"], aid)
    status = job["state"]
    if external and status == ("failed" if error else "done"):
        old = c.execute("SELECT text FROM messages WHERE id=?", (job["response"],)).fetchone()
        if old and old[0] == text:
            return {"ok": True, "id": job["response"]}
    if status != ("pending" if external else "working"):
        raise Problem(409, "这条任务已结束")
    name = c.execute("SELECT name FROM agents WHERE id=?", (aid,)).fetchone()[0]
    mid = message(c, job["room_id"], "error" if error else "agent", name, text, aid, job["root"])
    c.execute("UPDATE jobs SET state=?,response=? WHERE id=?", ("failed" if error else "done", mid, jid))
    return {"ok": True, "id": mid}
