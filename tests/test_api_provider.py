"""Real local HTTP round trips, without external traffic or model credentials."""
import contextlib
import http.server
import json
import pathlib
import sys
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from status_core import DEFAULTS
from codex_rpc import ClassificationError, CodexRPC

DECISION = {"state": "verify", "confidence": .96, "goal": "fix", "remaining": "test", "evidence": "code exists"}


@contextlib.contextmanager
def server(reply, status=200, delay=0, headers=None):
    requests = []
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            time.sleep(delay)
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            try:
                self.wfile.write(json.dumps(reply).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *args):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(srv.server_port) + "/v1", requests
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2)


def config(url, wire="chat", **extra):
    provider = {"base_url": url, "wire_api": wire, "env_key": "TEST_STATUS_KEY", **extra}
    return dict(DEFAULTS, backend="api", model_provider="gateway", model="custom-tiny",
                model_providers={"gateway": provider})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"TEST_STATUS_KEY": "private-test-key"})
        self.env.start()
        self.addCleanup(self.env.stop)
        # Must work without using any Codex classifier thread or model catalog.
        self.rpc = CodexRPC.__new__(CodexRPC)

    def test_chat_model_auth_request_caps_and_usage(self):
        reply = {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(DECISION)}}], "usage": {"total_tokens": 210}}
        with server(reply) as (url, requests):
            decision, usage = self.rpc.classify('{"messages":[]}', config(url))
            self.assertEqual(decision, DECISION)
            self.assertEqual(usage, 210)
            path, headers, body = requests[0]
            self.assertEqual(path, "/v1/chat/completions")
            self.assertEqual(headers["Authorization"], "Bearer private-test-key")
            self.assertEqual(body["model"], "custom-tiny")
            self.assertEqual(body["max_tokens"], 512)
            self.assertFalse(body["stream"])
            self.assertNotIn("tools", body)
            self.assertIn("SEMANTIC TASK STATUS", body["messages"][0]["content"])
            self.assertEqual(body["messages"][1]["content"], '{"messages":[]}')

    def test_responses_schema_and_usage(self):
        reply = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(DECISION)}]}],
                 "usage": {"input_tokens": 101, "output_tokens": 99}}
        with server(reply) as (url, requests):
            decision, usage = self.rpc.classify("{}", config(url, "responses", response_format="json_schema"))
            self.assertEqual((decision, usage), (DECISION, 200))
            path, _, body = requests[0]
            self.assertEqual(path, "/v1/responses")
            self.assertEqual(body["max_output_tokens"], 512)
            self.assertEqual(body["text"]["format"]["type"], "json_schema")
            self.assertFalse(body["store"])
            self.assertNotIn("tools", body)

    def test_malformed_model_json_keeps_usage(self):
        with server({"choices": [{"message": {"content": "invalid"}}], "usage": {"total_tokens": 9000}}) as (url, _):
            with self.assertRaises(ClassificationError) as caught:
                self.rpc.classify("{}", config(url))
            self.assertEqual(caught.exception.usage, 9000)

    def test_large_unicode_payload_is_not_cut_by_ipc_serialization(self):
        reply = {"choices": [{"message": {"content": json.dumps(DECISION)}}], "usage": {"total_tokens": 100}}
        payload = json.dumps({"goal": "汉字😀" * 6000}, ensure_ascii=False)
        with server(reply) as (url, requests):
            decision, _ = self.rpc.classify(payload, dict(config(url), max_input_chars=20000))
            self.assertEqual(decision, DECISION)
            self.assertEqual(requests[0][2]["messages"][1]["content"], payload)


    def test_tool_calls_are_rejected_even_with_valid_text(self):
        with server({"choices": [{"message": {"content": json.dumps(DECISION), "tool_calls": [{"id": "tool"}]}}], "usage": {"total_tokens": 42}}) as (url, _):
            with self.assertRaises(ClassificationError) as caught:
                self.rpc.classify("{}", config(url))
            self.assertEqual(caught.exception.usage, 42)

    def test_http_errors_never_echo_provider_body(self):
        with server({"error": "private-test-key secret transcript"}, status=401) as (url, _):
            with self.assertRaises(ClassificationError) as caught:
                self.rpc.classify("{}", config(url))
            self.assertNotIn("private-test-key", str(caught.exception))
            self.assertNotIn("transcript", str(caught.exception))

    def test_redirect_does_not_forward_credentials(self):
        with server({}) as (destination, leaked), server({}, status=307, headers={"Location": destination + "/stolen"}) as (url, original):
            with self.assertRaises(ClassificationError):
                self.rpc.classify("{}", config(url))
            self.assertEqual(len(original), 1)
            self.assertEqual(leaked, [])

    def test_missing_credential_does_not_send_request(self):
        with server({}) as (url, requests):
            with self.assertRaises(ClassificationError):
                self.rpc.classify("{}", config(url, env_key="UNSET_STATUS_KEY_FOR_TEST"))
            self.assertEqual(requests, [])

    def test_absolute_timeout_leaves_unknown_usage(self):
        with server({}, delay=2) as (url, _):
            cfg = dict(config(url), classification_timeout_seconds=1)
            start = time.monotonic()
            with self.assertRaises(ClassificationError) as caught:
                self.rpc.classify("{}", cfg)
            self.assertLess(time.monotonic() - start, 1.8)
            self.assertIsNone(caught.exception.usage)


if __name__ == "__main__":
    unittest.main()
