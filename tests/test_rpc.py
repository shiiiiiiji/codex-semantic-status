"""The real classifier adapter with recorded protocol events, without cloud calls."""
import pathlib
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
from codex_rpc import ClassificationError, CodexRPC
from status_core import DEFAULTS


class ProtocolFixture(CodexRPC):
    def __init__(self, events):
        self.notifications = []
        self.events = list(events)

    def request(self, method, params, timeout=12):
        if method == "model/list":
            return {"data": [{"model": "gpt-6-luna"}]}
        if method == "skills/list":
            return {"data": []}
        if method == "thread/start":
            return {"model": "gpt-6-luna", "thread": {"id": "classifier"}}
        if method == "turn/start":
            return {"turn": {"id": "classification-turn"}}
        if method in ["turn/interrupt", "thread/unsubscribe"] and params["threadId"] == "classifier":
            return {}
        raise AssertionError("Unexpected RPC at external boundary: " + method)

    def receive(self, timeout):
        if not self.events:
            raise TimeoutError("fixture exhausted")
        return self.events.pop(0)


class RpcTests(unittest.TestCase):
    def test_inactive_definition_cannot_override_the_inherited_provider(self):
        class InheritedFixture(ProtocolFixture):
            def request(self, method, params, timeout=12):
                if method == "thread/start":
                    self.params = params
                return super().request(method, params, timeout)
        rpc = InheritedFixture([
            {"method": "item/completed", "params": {"threadId": "classifier", "item": {"type": "agentMessage", "text": "{}"}}},
            {"method": "turn/completed", "params": {"threadId": "classifier", "turn": {"status": "completed"}}}])
        cfg = dict(DEFAULTS, model_providers={"openai": {"base_url": "https://inactive.example/v1", "env_key": "INACTIVE_KEY", "wire_api": "chat"}})
        with patch("codex_rpc.tomllib.loads", return_value={}):
            rpc.classify("{}", cfg)
        for native in rpc.params["config"]["model_providers"].values():
            self.assertNotIn("base_url", native)
            self.assertNotIn("env_key", native)
            self.assertNotIn("wire_api", native)

    def test_custom_provider_model_skips_inherited_catalog_and_is_isolated(self):
        class CustomFixture(ProtocolFixture):
            def request(self, method, params, timeout=12):
                if method == "model/list":
                    raise AssertionError("wrong provider catalog")
                if method == "thread/start":
                    self.params = params
                    return {"model": "custom-tiny", "modelProvider": "gateway", "thread": {"id": "classifier"}}
                return super().request(method, params, timeout)
        rpc = CustomFixture([
            {"method": "item/completed", "params": {"threadId": "classifier", "item": {"type": "agentMessage", "text": "{}"}}},
            {"method": "turn/completed", "params": {"threadId": "classifier", "turn": {"status": "completed"}}}])
        cfg = dict(DEFAULTS, model="custom-tiny", model_provider="gateway",
                   model_providers={"gateway": {"base_url": "https://example.com/v1", "wire_api": "responses",
                       "env_key": "TEST_KEY", "default_model": "custom-tiny", "response_format": "none"}})
        rpc.classify("{}", cfg)
        self.assertEqual(rpc.params["modelProvider"], "gateway")
        self.assertFalse(rpc.params["allowProviderModelFallback"])
        native = rpc.params["config"]["model_providers"]["gateway"]
        self.assertNotIn("default_model", native)
        self.assertNotIn("response_format", native)
        self.assertEqual(native["env_key"], "TEST_KEY")

    def test_failed_turn_preserves_observed_usage_on_the_exception(self):
        rpc = ProtocolFixture([
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "classifier",
                "tokenUsage": {"total": {"totalTokens": 40000}}}},
            {"method": "turn/completed", "params": {"threadId": "classifier",
                "turn": {"id": "classification-turn", "status": "failed"}}},
        ])
        with self.assertRaises(ClassificationError) as caught:
            rpc.classify("{}", DEFAULTS)
        self.assertEqual(caught.exception.usage, 40000)

    def test_malformed_json_preserves_observed_usage(self):
        rpc = ProtocolFixture([
            {"method": "thread/tokenUsage/updated", "params": {"threadId": "classifier",
                "tokenUsage": {"total": {"totalTokens": 24000}}}},
            {"method": "item/completed", "params": {"threadId": "classifier",
                "item": {"type": "agentMessage", "text": "malformed"}}},
            {"method": "turn/completed", "params": {"threadId": "classifier",
                "turn": {"status": "completed"}}},
        ])
        with self.assertRaises(ClassificationError) as caught:
            rpc.classify("{}", DEFAULTS)
        self.assertEqual(caught.exception.usage, 24000)


if __name__ == "__main__":
    unittest.main()
