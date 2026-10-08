"""Management commands must be portable, read-only by default and privacy-preserving."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from status_store import Store
from codex_rpc import CodexRPC, RpcError


class ModelFixture(CodexRPC):
    def models(self):
        return [{"model": "expensive-large"}, {"model": "gpt-example-mini"}]


class ManagementTests(unittest.TestCase):
    def run_cli(self, directory, *args):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/semantic_status.py"),
            "--data-dir", str(directory), *args], text=True, capture_output=True, timeout=5)

    def test_status_does_not_create_runtime_storage_when_unused(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp) / "unused"
            result = self.run_cli(directory, "status")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["usage"], [])
            self.assertFalse(directory.exists())

    def test_invalid_config_keeps_the_previous_file_intact(self):
        with tempfile.TemporaryDirectory() as temp:
            p = pathlib.Path(temp) / "config.json"
            p.write_text('{"max_tokens_per_day":12345}\n')
            before = p.read_bytes()
            result = self.run_cli(temp, "configure", "--set", "max_tokens_per_day=-1")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(p.read_bytes(), before)

    def test_auto_model_uses_an_advertised_lightweight_alternative(self):
        self.assertEqual(ModelFixture.__new__(ModelFixture).select_model("auto"), "gpt-example-mini")

    def test_explicit_unknown_model_does_not_fall_back(self):
        with self.assertRaises(RpcError):
            ModelFixture.__new__(ModelFixture).select_model("missing-model")

    def test_zero_budget_avoids_starting_an_external_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = pathlib.Path(temp)
            (directory / "config.json").write_text(json.dumps({"debounce_seconds": 0,
                "max_calls_per_day": 0, "codex_command": "/does-not-exist"}))
            env = dict(os.environ, SEMANTIC_STATUS_DATA=temp, PLUGIN_ROOT=str(ROOT))
            env.pop("CODEX_SEMANTIC_STATUS_CLASSIFIER", None)
            event = {"hook_event_name": "Stop", "session_id": "example", "turn_id": "one"}
            r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/semantic_status.py"), "hook"],
                input=json.dumps(event), text=True, capture_output=True, env=env, timeout=5)
            self.assertEqual(r.returncode, 0)
            import time
            until = time.monotonic() + 3
            store = Store(directory)
            while time.monotonic() < until:
                report = store.report()
                if report["pending"] == 0:
                    break
                time.sleep(.02)
            self.assertEqual(report["usage"], [])
            self.assertEqual(report["threads"][0]["last_result"], "budget")

    def test_failure_diagnostics_contain_error_type_without_exception_text(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(pathlib.Path(temp))
            from status_core import DEFAULTS
            store.enqueue({"session_id": "example", "turn_id": "one"}, DEFAULTS, 1000)
            store.discard("example", 1, "classification_failed", "TimeoutError")
            result = self.run_cli(temp, "logs", "--limit", "1")
            self.assertEqual(result.returncode, 0, result.stderr)
            entries = json.loads(result.stdout)
            self.assertEqual(entries[0]["result"], "classification_failed")
            self.assertEqual(entries[0]["error_type"], "TimeoutError")
            self.assertNotIn("title", entries[0])
            self.assertNotIn("message", entries[0])


if __name__ == "__main__":
    unittest.main()
