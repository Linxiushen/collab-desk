"""Regression coverage for the message history passed to local and external agents."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import store


class ConversationContextTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        data_path = patch.object(store, "DATA", Path(directory.name))
        data_path.start()
        self.addCleanup(data_path.stop)
        store.init()

    def test_queued_followup_receives_previous_answer_and_its_own_peer(self):
        with store.db(True) as db:
            first = store.send(db, "lobby", {"text": "请给我一个库存建议"})["id"]
            second = store.send(db, "lobby", {"text": "把建议改成一句话"})["id"]
            third = store.send(db, "lobby", {"text": "明天的预算呢？"})["id"]
            store.message(db, "lobby", "agent", "分析员", "未来轮次的回复", "analyst", third)
            late = store.message(db, "lobby", "agent", "分析员", "先盘点现货", "analyst", first)
            peer = store.message(db, "lobby", "agent", "分析员", "建议先盘点", "analyst", second)
            history = store.context(db, {"room_id": "lobby", "root": second})
        self.assertEqual([entry["id"] for entry in history], [first, late, second, peer])

    def test_long_current_round_keeps_question_and_latest_peer_replies(self):
        with store.db(True) as db:
            store.send(db, "lobby", {"text": "之前的问题"})
            current = store.send(db, "lobby", {"text": "整理本轮反馈"})["id"]
            replies = [store.message(db, "lobby", "agent", "复核员", str(i), "reviewer", current)
                       for i in range(75)]
            history = store.context(db, {"room_id": "lobby", "root": current})
        self.assertEqual([entry["id"] for entry in history], [current] + replies[-59:])


if __name__ == "__main__":
    unittest.main()
