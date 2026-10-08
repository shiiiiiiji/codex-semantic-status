"""Execute the real hook, real detached worker, and SQLite with a slow RPC fixture."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


class HookTests(unittest.TestCase):
    def test_slow_background_reader_does_not_delay_hook_or_use_model_tokens(self):
        with tempfile.TemporaryDirectory() as temp:
            d = pathlib.Path(temp)
            rpc = d / "fake-codex"
            rpc.write_text('#!' + sys.executable + '\n' + '''
import sys,json,time,pathlib
for line in sys.stdin:
 obj=json.loads(line)
 if 'id' not in obj:continue
 if obj['method']=='thread/read':
  pathlib.Path(__file__).with_name('reading').touch()
  time.sleep(2)
  reply={'id':obj['id'],'error':{'code':-32600,'message':'test thread absent'}}
 else:reply={'id':obj['id'],'result':{}}
 print(json.dumps(reply),flush=True)
''')
            rpc.chmod(0o700)
            cfg = {"debounce_seconds": 0, "codex_command": str(rpc)}
            (d / "config.json").write_text(json.dumps(cfg))
            env = dict(os.environ, SEMANTIC_STATUS_DATA=str(d), PLUGIN_ROOT=str(ROOT))
            env.pop("CODEX_SEMANTIC_STATUS_CLASSIFIER", None)
            start = time.monotonic()
            r = subprocess.run(["sh", str(ROOT / "scripts/run-hook.sh")], input=json.dumps({
                "session_id": "test-thread", "turn_id": "test-turn", "hook_event_name": "Stop"}),
                text=True, capture_output=True, timeout=2, env=env)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout), {})
            self.assertEqual(r.stderr, "")
            self.assertLess(time.monotonic() - start, 1.5)
            sys.path.insert(0, str(ROOT / "scripts"))
            from status_store import Store
            end = time.monotonic() + 5
            while time.monotonic() < end:
                report = Store(d).report()
                if (d / "reading").exists() and report["pending"] == 0:
                    break
                time.sleep(.05)
            self.assertTrue((d / "reading").exists())
            self.assertEqual(report["pending"], 0)
            self.assertEqual(report["usage"], [])

    def test_malformed_event_is_silent_and_does_not_create_a_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            env = dict(os.environ, SEMANTIC_STATUS_DATA=temp, PLUGIN_ROOT=str(ROOT))
            r = subprocess.run(["sh", str(ROOT / "scripts/run-hook.sh")], input="not-json",
                               text=True, capture_output=True, env=env, timeout=2)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(json.loads(r.stdout), {})
            self.assertEqual(r.stderr, "")
            self.assertFalse((pathlib.Path(temp) / "state.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
