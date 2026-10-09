"""Real queue/worker tests; only the external Codex boundary is replaced."""
import pathlib
import sys
import tempfile
import unittest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from status_core import DEFAULTS
from status_store import Store
try:
    from status_worker import process_job
except ImportError:
    process_job = None


class ExternalCodex:
    def __init__(self):
        self.name = "修复登录"
        self.version = "v1"
        self.after_classification = None
        self.inferences = 0
        self.decision = {"state": "verify", "confidence": .96, "goal": "修复登录",
                         "remaining": "待验证", "evidence": "实现已写好，尚未运行测试"}

    def snapshot(self, tid):
        return {"title": self.name, "version": self.version, "fingerprint": self.version,
                "goal": "修复登录", "messages": [{"role": "assistant", "text": "已实现，还没测试"}], "ready": True}

    def classify(self, payload, config):
        self.inferences += 1
        if self.after_classification:
            self.after_classification()
        return dict(self.decision), 1500

    def set_name(self, tid, name):
        self.name = name


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(pathlib.Path(self.tmp.name))
        self.cfg = dict(DEFAULTS, debounce_seconds=0, min_interval_seconds=0)
        self.rpc = ExternalCodex()

    def run_job(self, turn="t1"):
        if process_job is None:
            self.fail("background processor is not implemented")
        self.store.enqueue({"session_id": "s1", "turn_id": turn}, self.cfg, 1000)
        return process_job(self.store, self.rpc, self.store.next_job(1000), self.cfg, now=1000)

    def test_valid_decision_updates_only_the_title(self):
        self.assertEqual(self.run_job(), "updated")
        self.assertEqual(self.rpc.name, "🧪 修复登录")
        self.assertEqual(self.store.current("s1")["decision"]["state"], "verify")

    def test_new_content_during_inference_discards_the_old_result(self):
        self.rpc.after_classification = lambda: setattr(self.rpc, "version", "v2")
        self.assertEqual(self.run_job(), "stale")
        self.assertEqual(self.rpc.name, "修复登录")
        self.assertIsNotNone(self.store.pending_due())

    def test_manual_rename_during_inference_is_preserved(self):
        self.rpc.after_classification = lambda: setattr(self.rpc, "name", "我的新标题")
        self.assertEqual(self.run_job(), "manual_title")
        self.assertEqual(self.rpc.name, "我的新标题")
        self.assertTrue(self.store.current("s1")["paused"])

    def test_same_content_on_a_later_event_does_not_spend_again(self):
        self.run_job()
        self.assertEqual(self.run_job("t2"), "unchanged")
        self.assertEqual(self.rpc.inferences, 1)

    def test_timeout_keeps_the_title_and_reserved_usage(self):
        def fail():
            raise TimeoutError("classifier")
        self.rpc.after_classification = fail
        self.assertEqual(self.run_job(), "classification_failed")
        self.assertEqual(self.rpc.name, "修复登录")
        self.assertEqual(self.store.report()["usage"][0]["tokens"], 20000)

    def test_budget_exhaustion_makes_no_model_request(self):
        self.cfg["max_calls_per_day"] = 0
        self.assertEqual(self.run_job(), "budget")
        self.assertEqual(self.rpc.inferences, 0)
        self.assertEqual(self.rpc.name, "修复登录")

    def test_api_uses_its_own_reservation_and_retains_failure_usage(self):
        self.cfg.update(backend="api", max_tokens_per_day=9000)
        def fail():
            error = RuntimeError("provider failure")
            error.usage = 10000
            raise error
        self.rpc.after_classification = fail
        self.assertEqual(self.run_job(), "classification_failed")
        self.assertEqual(self.rpc.inferences, 1)
        self.assertEqual(self.store.report()["usage"][0]["tokens"], 10000)

    def test_api_timeout_retains_api_reservation(self):
        self.cfg.update(backend="api", max_tokens_per_day=9000)
        self.rpc.after_classification = lambda: (_ for _ in ()).throw(TimeoutError())
        self.assertEqual(self.run_job(), "classification_failed")
        self.assertEqual(self.store.report()["usage"][0]["tokens"], 8000)

    def test_manual_rename_after_a_previous_update_pauses_the_thread(self):
        self.run_job()
        self.rpc.name = "🚩 人工重点"
        self.assertEqual(self.run_job("t2"), "manual_title")
        self.assertEqual(self.rpc.inferences, 1)

    def test_new_hook_revision_during_inference_cannot_be_consumed(self):
        self.rpc.after_classification = lambda: self.store.enqueue({"session_id": "s1", "turn_id": "t2"}, self.cfg, 1001)
        self.assertEqual(self.run_job(), "stale")
        self.assertEqual(self.rpc.name, "修复登录")
        self.assertEqual(self.store.next_job(1001)["revision"], 2)

    def test_stale_content_retry_preserves_the_paid_call_cooldown(self):
        self.cfg["min_interval_seconds"] = 300
        self.rpc.after_classification = lambda: setattr(self.rpc, "version", "v2")
        self.assertEqual(self.run_job(), "stale")
        self.assertIsNone(self.store.next_job(1299))
        self.assertIsNotNone(self.store.next_job(1300))

    def test_known_usage_from_a_failed_inference_still_enforces_the_daily_budget(self):
        def fail():
            error = RuntimeError("failed after usage notification")
            error.usage = 40000
            raise error
        self.rpc.after_classification = fail
        self.assertEqual(self.run_job(), "classification_failed")
        self.assertEqual(self.store.report()["usage"][0]["tokens"], 40000)
        self.assertIsNone(self.store.reserve("another-thread", self.cfg, 1001))

    def test_even_high_confidence_completion_is_refused_for_truncated_requirements(self):
        self.rpc.decision = {"state": "completed", "confidence": .99, "goal": "完成长要求",
                             "remaining": "", "evidence": "助手声称完成"}
        original = self.rpc.snapshot
        def snapshot(tid):
            s = original(tid)
            s["messages"] = [{"role": "user", "text": "长要求" * 10000},
                             {"role": "assistant", "text": "已完成"}]
            return s
        self.rpc.snapshot = snapshot
        self.assertEqual(self.run_job(), "uncertain")
        self.assertEqual(self.rpc.name, "修复登录")

    def test_cached_short_summary_does_not_erase_missing_original_requirements(self):
        original = self.rpc.snapshot
        def snapshot(tid):
            result = original(tid)
            result["goal"] = "原始完整要求" * 4000
            result["messages"] = [{"role": "user", "text": "继续上一项工作"},
                                  {"role": "assistant", "text": "最新进展"}]
            return result
        self.rpc.snapshot = snapshot
        self.rpc.decision = {"state": "in_progress", "confidence": .99, "goal": "短摘要",
                             "remaining": "完成剩余工作", "evidence": "正在推进"}
        self.assertEqual(self.run_job(), "updated")
        self.rpc.version = "v2"
        self.rpc.decision = {"state": "completed", "confidence": .99, "goal": "短摘要",
                             "remaining": "", "evidence": "助手声称完成"}
        self.assertEqual(self.run_job("t2"), "uncertain")
        self.assertEqual(self.rpc.name, "▶️ 修复登录")


if __name__ == "__main__":
    unittest.main()
