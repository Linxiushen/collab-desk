"""Regression coverage for visible activity and recoverable external failures."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server
import store


class RoomAPITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = patch.object(store, "DATA", Path(self.tmp.name))
        self.data.start()
        store.init()
        self.auth = server.owner_request("/api/agents", True, {"name": "测试外部成员"})
        self.other = server.owner_request("/api/agents", True, {"name": "另一个成员"})
        server.owner_request("/api/rooms/lobby/members", True, {"agent_ids": ["analyst", "reviewer", self.auth["id"]]})

    def tearDown(self):
        self.data.stop()
        self.tmp.cleanup()

    def post(self, value, targets):
        return server.owner_request("/api/rooms/lobby/messages", True, {"text": value, "targets": targets})

    def external(self, path, body=None, actor=None):
        return server.agent_request("/api/agent/" + path, "Bearer " + (actor or self.auth)["token"],
                                    body is not None, body or {})

    def test_activity_distinguishes_queue_working_and_completed(self):
        self.post("检查这条订单", ["analyst", "reviewer"])
        with store.db(True) as c:
            job = c.execute("SELECT * FROM jobs WHERE agent_id='analyst'").fetchone()
            c.execute("UPDATE jobs SET state='working' WHERE id=?", (job["id"],))
        room = server.owner_request("/api/rooms/lobby", False, {})
        self.assertEqual(room["member_activity"]["analyst"]["working"], 1)
        self.assertEqual(room["member_activity"]["reviewer"]["pending"], 1)
        self.assertIsNone(room["member_activity"][self.auth["id"]]["worker_status"])
        with store.db(True) as c:
            store.finish(c, job["id"], "analyst", "已检查。")
        room = server.owner_request("/api/rooms/lobby", False, {})
        self.assertEqual(room["member_activity"]["analyst"]["working"], 0)
        self.assertIsNotNone(room["member_activity"]["analyst"]["last_reply_at"])

    def test_failed_external_job_is_visible_idempotent_and_explicitly_retryable(self):
        self.post("请核算库存", [self.auth["id"]])
        job = self.external("inbox")["jobs"][0]
        route = "jobs/" + str(job["id"])
        failure = {"text": "本次回复中断，请重试。"}
        first = self.external(route + "/fail", failure)
        self.assertEqual(first, self.external(route + "/fail", failure))
        self.assertEqual([], self.external("inbox")["jobs"])
        room = server.owner_request("/api/rooms/lobby", False, {})
        self.assertEqual(room["waiting_external"], 0)
        self.assertEqual(room["messages"][-1]["kind"], "error")
        with self.assertRaises(store.Problem) as denied:
            self.external(route + "/retry", {}, actor=self.other)
        self.assertEqual(denied.exception.status, 404)
        retried = self.external(route + "/retry", {})
        self.assertEqual(retried["job"]["prompt"], "请核算库存")
        self.assertEqual(retried["job"]["id"], job["id"])
        self.external(route + "/reply", {"text": "库存为 8 件。"})
        self.assertEqual(self.external("inbox")["jobs"], [])
        with self.assertRaises(store.Problem):
            self.external(route + "/retry", {})

    def test_unavailable_model_keeps_new_work_queued_until_ready(self):
        self.post("等待模型后再算", ["analyst"])
        checked = threading.Event()
        answered = threading.Event()

        class Model:
            ready = False
            def status(self):
                checked.set()
                return {"model_status": "ready" if self.ready else "unavailable"}
            def reply(self, agent, history):
                answered.set()
                return "模型就绪后完成。"

        model = Model()
        stop, pulse = threading.Event(), threading.Event()
        with patch.object(server, "ENGINE", model), patch.object(server, "STOP", stop), patch.object(server, "PULSE", pulse):
            thread = threading.Thread(target=server.infer)
            thread.start()
            try:
                self.assertTrue(checked.wait(2))
                self.assertFalse(answered.is_set())
                with store.db() as c:
                    self.assertEqual(c.execute("SELECT state FROM jobs").fetchone()[0], "pending")
                model.ready = True
                self.assertTrue(answered.wait(4))
            finally:
                stop.set()
                pulse.set()
                thread.join(3)
        with store.db() as c:
            self.assertEqual(c.execute("SELECT state FROM jobs").fetchone()[0], "done")


if __name__ == "__main__":
    unittest.main()
