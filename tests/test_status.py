"""Behavior tests: duplicate spend, unsafe completion, and lost/manual titles."""
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
try:
    from status_core import DEFAULTS, context_payload, managed_title, validate_decision
    from status_store import Store
except ImportError:
    DEFAULTS = {}
    context_payload = managed_title = validate_decision = Store = None


class CoreTests(unittest.TestCase):
    def test_updates_one_status_prefix_without_losing_the_title(self):
        actual = managed_title("▶️ 修复登录异常", "verify") if managed_title else None
        self.assertEqual(actual, "🧪 修复登录异常")

    def test_keeps_a_user_priority_flag(self):
        actual = managed_title("🚩 圈人交互优化", "completed") if managed_title else "missing"
        self.assertIsNone(actual)

    def test_uncertain_completion_cannot_change_the_title(self):
        d = {"state": "completed", "confidence": .85, "goal": "修复", "remaining": "", "evidence": "回复结束"}
        actual = validate_decision(d, DEFAULTS) if validate_decision else "missing"
        self.assertIsNone(actual)

    def test_invalid_state_cannot_become_a_title(self):
        actual = validate_decision({"state": "🔥 hacked", "confidence": 1}, DEFAULTS) if validate_decision else "missing"
        self.assertIsNone(actual)

    def test_completion_with_remaining_work_is_rejected(self):
        d = {"state": "completed", "confidence": .99, "goal": "部署", "remaining": "待部署", "evidence": "测试通过"}
        actual = validate_decision(d, DEFAULTS) if validate_decision else "missing"
        self.assertIsNone(actual)

    def test_context_is_bounded_and_retains_the_latest_user_correction(self):
        turns = [{"role": "user", "text": "x" * 20000}, {"role": "user", "text": "新要求：必须合入才算完成"}]
        actual = context_payload("部署目标", turns, {}, 1500) if context_payload else ""
        self.assertLessEqual(len(actual), 1500)
        self.assertIn("必须合入才算完成", actual)
        self.assertIn("部署目标", actual)

    def test_latest_user_scope_is_not_cut_when_it_fits_the_budget(self):
        text = "前" * 1100 + "必须合入 main 才算完成" + "后" * 1100
        actual = context_payload("修复登录", [{"role": "user", "text": text}], {}, 6000)
        self.assertIn("必须合入 main 才算完成", actual)
        self.assertEqual(json.loads(actual)["messages"][0]["text"], text)

    def test_completion_is_refused_when_user_requirements_are_truncated(self):
        payload = context_payload("修复登录", [{"role": "user", "text": "长要求" * 10000}], {}, 1500)
        self.assertTrue(json.loads(payload).get("incomplete_user_context"))

    def test_complete_user_message_can_supply_the_full_long_goal(self):
        text = "需求" * 800 + "全部完成后验收"
        payload = context_payload(text, [{"role": "user", "text": text}], {}, 6000)
        self.assertFalse(json.loads(payload)["incomplete_user_context"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(pathlib.Path(self.tmp.name)) if Store else None
        self.cfg = dict(DEFAULTS, debounce_seconds=60, min_interval_seconds=300,
                        max_calls_per_day=2, max_calls_per_thread_per_day=2,
                        max_tokens_per_day=20000, token_reservation=10000)

    def test_duplicate_stop_does_not_create_another_job(self):
        if not self.store:
            self.fail("durable queue is not implemented")
        e = {"session_id": "s1", "turn_id": "t1", "hook_event_name": "Stop"}
        self.store.enqueue(e, self.cfg, 1000)
        self.store.enqueue(e, self.cfg, 1001)
        self.assertIsNone(self.store.next_job(1059))
        job = self.store.next_job(1060)
        self.assertEqual(job["revision"], 1)

    def test_new_events_extend_the_debounce_window(self):
        if not self.store:
            self.fail("durable queue is not implemented")
        self.store.enqueue({"session_id": "s1", "turn_id": "t1"}, self.cfg, 1000)
        self.store.enqueue({"session_id": "s1", "turn_id": "t2"}, self.cfg, 1030)
        self.assertIsNone(self.store.next_job(1089))
        self.assertEqual(self.store.next_job(1090)["revision"], 2)

    def test_budget_survives_another_store_instance(self):
        if not self.store:
            self.fail("budget ledger is not implemented")
        self.assertIsNotNone(self.store.reserve("a", self.cfg, 1000))
        self.assertIsNotNone(self.store.reserve("b", self.cfg, 1000))
        again = Store(pathlib.Path(self.tmp.name))
        self.assertIsNone(again.reserve("c", self.cfg, 1000))

    def test_unknown_usage_is_not_refunded(self):
        if not self.store:
            self.fail("budget ledger is not implemented")
        r = self.store.reserve("a", self.cfg, 1000)
        self.store.settle(r, None)
        self.store.reserve("b", self.cfg, 1000)
        self.assertIsNone(self.store.reserve("c", self.cfg, 1000))

    def test_real_usage_releases_only_unused_reserved_tokens(self):
        if not self.store:
            self.fail("budget ledger is not implemented")
        cfg = dict(self.cfg, max_calls_per_day=10, max_tokens_per_day=12000)
        r = self.store.reserve("a", cfg, 1000)
        self.store.settle(r, 1500)
        self.assertIsNotNone(self.store.reserve("b", cfg, 1000))
        self.assertIsNone(self.store.reserve("c", cfg, 1000))

    def test_old_worker_cannot_finish_a_newer_revision(self):
        if not self.store:
            self.fail("revision protection is not implemented")
        self.store.enqueue({"session_id": "s1", "turn_id": "t1"}, self.cfg, 1000)
        self.store.enqueue({"session_id": "s1", "turn_id": "t2"}, self.cfg, 1001)
        self.assertFalse(self.store.finish("s1", 1, "old", {}, "✅ title"))
        self.assertEqual(self.store.next_job(1100)["revision"], 2)

    def test_call_admission_cannot_bypass_the_thread_cooldown(self):
        self.assertIsNotNone(self.store.reserve("a", self.cfg, 1000))
        self.assertIsNone(self.store.reserve("a", self.cfg, 1100))
        self.assertIsNotNone(self.store.reserve("a", self.cfg, 1300))


if __name__ == "__main__":
    unittest.main()
