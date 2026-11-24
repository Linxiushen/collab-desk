"""Exercise inbox, failure, retry and journal recovery against an isolated room store."""
import sqlite3
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_cli
import codex_worker as worker
import server
import store


class WorkerTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.patched(store, "DATA", new=Path(directory.name))
        store.init()
        with store.db(True) as db:
            self.agent = store.add_agent(db, {"name": "测试成员"})
            store.put_members(db, "lobby", [self.agent["id"]])
        self.config = {"token": self.agent["token"]}
        self.journal = sqlite3.connect(":memory:")
        self.addCleanup(self.journal.close)
        self.journal.execute("CREATE TABLE attempts(id TEXT PRIMARY KEY, draft TEXT)")
        self.http = self.patched(worker, "request", side_effect=self.route)
        self.client = self.patched(agent_cli, "request", side_effect=self.route)
        self.generate = self.patched(worker, "codex_reply", return_value="已核对。")
        self.ready = self.patched(worker, "local_model_ready", return_value=True)

    def patched(self, module, key, **kwargs):
        replacement = patch.object(module, key, **kwargs)
        self.addCleanup(replacement.stop)
        return replacement.start()

    def route(self, config, path, data=None):
        try:
            return server.agent_request("/api/agent/" + path, "Bearer " + config["token"], data is not None, data or {})
        except store.Problem as exc:
            raise worker.ClientError(exc.message) from exc

    def add(self):
        with store.db(True) as db:
            root = store.send(db, "lobby", {"text": "请检查库存"})["id"]
            return str(db.execute("SELECT id FROM jobs WHERE root=?", (root,)).fetchone()[0])

    def state(self, jid):
        with store.db() as db:
            return db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0]

    def once(self, retry=None):
        return worker.run_once(self.config, self.agent, self.journal, retry)

    def test_interruption_survives_restart_and_retry_regenerates_once(self):
        old = self.add()
        new = self.add()
        self.journal.execute("INSERT INTO attempts VALUES(?,NULL)", (old,))
        self.once()
        self.assertEqual((self.state(old), self.state(new)), ("failed", "done"))
        self.assertEqual(self.generate.call_count, 1)
        self.assertFalse(self.once())
        self.once(old)
        self.assertEqual(self.state(old), "done")
        self.assertEqual(self.generate.call_count, 2)
        self.assertEqual(sum(c.args[1].endswith("/retry") for c in self.http.call_args_list), 1)
        with store.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM messages WHERE kind='error'").fetchone()[0], 1)
        with self.assertRaises(worker.ClientError):
            self.once(old)
        self.assertEqual(self.generate.call_count, 2)

    def test_bad_generations_report_failure_and_watch_moves_on(self):
        first, second, third = self.add(), self.add(), self.add()
        self.generate.side_effect = [worker.ClientError("Codex 退出"), subprocess.TimeoutExpired("codex", 75), "下一条已回复"]
        self.once()
        self.assertEqual([self.state(j) for j in [first, second, third]], ["failed", "failed", "done"])
        self.assertEqual(self.journal.execute("SELECT count(*) FROM attempts WHERE draft IS NULL").fetchone()[0], 2)

    def test_sigint_reports_failure_and_propagates(self):
        jid = self.add()
        self.generate.side_effect = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.once()
        self.assertEqual(self.state(jid), "failed")

    def test_submission_failure_preserves_draft_even_without_model(self):
        jid = self.add()

        def disconnect(config, path, data=None):
            if path.endswith("/reply"):
                raise worker.ClientError("连接断开")
            return self.route(config, path, data)

        self.client.side_effect = disconnect
        with self.assertRaises(worker.ClientError):
            self.once()
        self.assertEqual(self.state(jid), "pending")
        self.assertEqual(self.journal.execute("SELECT draft FROM attempts WHERE id=?", (jid,)).fetchone()[0], "已核对。")
        self.ready.return_value = False
        self.client.side_effect = self.route
        self.once()
        self.assertEqual(self.state(jid), "done")
        self.assertEqual(self.generate.call_count, 1)

    def test_offline_model_leaves_unattempted_jobs_pending(self):
        jid = self.add()
        self.ready.return_value = False
        self.assertFalse(self.once())
        self.assertEqual(self.state(jid), "pending")
        self.assertEqual(self.journal.execute("SELECT count(*) FROM attempts").fetchone()[0], 0)
        self.generate.assert_not_called()
        self.ready.return_value = True
        self.once()
        self.assertEqual(self.state(jid), "done")


if __name__ == "__main__":
    unittest.main()
