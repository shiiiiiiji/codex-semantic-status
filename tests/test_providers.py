"""Custom providers must stay independent of source-thread settings and credentials."""
import json
import argparse
import contextlib
import io
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from status_core import DEFAULTS, validate_config
from provider_config import credential, save_key
from provider_cli import manage


class ProviderConfigurationTests(unittest.TestCase):
    def cli(self, directory, *args, input=None):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/semantic_status.py"),
            "--data-dir", str(directory), *args], input=input, capture_output=True, text=True, timeout=5)

    def test_provider_commands_register_select_remove_and_restore_default(self):
        with tempfile.TemporaryDirectory() as temp:
            for args in [("provider", "add", "gateway", "--base-url", "https://example.com/v1", "--model", "tiny", "--env-key", "EXAMPLE_KEY"),
                         ("provider", "use", "gateway")]:
                result = self.cli(temp, *args)
                self.assertEqual(result.returncode, 0, result.stderr)
            cfg = json.loads((pathlib.Path(temp) / "config.json").read_text())
            self.assertEqual((cfg["backend"], cfg["model"]), ("api", "tiny"))
            result = self.cli(temp, "provider", "list")
            self.assertTrue(json.loads(result.stdout)["providers"][0]["selected"])
            self.assertNotEqual(self.cli(temp, "provider", "remove", "gateway").returncode, 0)
            self.assertEqual(self.cli(temp, "provider", "reset").returncode, 0)
            self.assertEqual(self.cli(temp, "provider", "remove", "gateway").returncode, 0)
            cfg = json.loads((pathlib.Path(temp) / "config.json").read_text())
            self.assertEqual((cfg["backend"], cfg["model_provider"], cfg["model"]), ("codex", "", "auto"))
            self.assertEqual(cfg["max_tokens_per_day"], 50000)

    def test_provider_setup_wizard_can_configure_without_network(self):
        with tempfile.TemporaryDirectory() as temp:
            r = self.cli(temp, "provider", "setup", input="gateway\nhttps://example.com/v1\ntiny\nchat\nnone\n")
            self.assertEqual(r.returncode, 0, r.stderr)
            cfg = json.loads((pathlib.Path(temp) / "config.json").read_text())
            self.assertEqual((cfg["backend"], cfg["model_provider"], cfg["model"]), ("api", "gateway", "tiny"))

    def test_wizard_hidden_key_stays_out_of_config_and_output(self):
        with tempfile.TemporaryDirectory() as temp:
            printed = io.StringIO()
            args = argparse.Namespace(provider_command="setup")
            with patch("builtins.input", side_effect=["gateway", "https://example.com/v1", "tiny", "chat", "file"]), \
                    patch("provider_cli.sys.stdin.isatty", return_value=True), \
                    patch("provider_cli.getpass.getpass", return_value="private-test-key"), contextlib.redirect_stdout(printed):
                result = manage(args, temp, DEFAULTS)
            saved = (pathlib.Path(temp) / "config.json").read_text()
            self.assertNotIn("private-test-key", saved + printed.getvalue() + json.dumps(result))
            key_file = pathlib.Path(json.loads(saved)["model_providers"]["gateway"]["api_key_file"])
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
            self.assertEqual(credential({"api_key_file": str(key_file)}), "private-test-key")
            self.assertEqual(DEFAULTS["model_providers"], {})

    def test_api_doctor_does_not_need_codex_or_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            cfg = dict(DEFAULTS, backend="api", codex_command="/does-not-exist", model_provider="gw", model="tiny",
                       model_providers={"gw": {"base_url": "https://example.com/v1", "env_key": "UNSET_STATUS_KEY_FOR_TEST"}})
            (pathlib.Path(temp) / "config.json").write_text(json.dumps(cfg))
            r = self.cli(temp, "doctor")
            self.assertEqual(r.returncode, 0, r.stderr)
            report = json.loads(r.stdout)
            self.assertFalse(report["credential_ready"])
            self.assertFalse(report["calls_model"])
            self.assertEqual(report["effective_token_reservation"], 8000)

    def test_credentials_are_saved_with_private_permissions_outside_git(self):
        with tempfile.TemporaryDirectory() as temp:
            path = pathlib.Path(save_key(temp, "gateway", "private-test-key"))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(credential({"api_key_file": str(path)}), "private-test-key")
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                credential({"api_key_file": str(path)})
            (pathlib.Path(temp) / ".git").mkdir()
            with self.assertRaises(ValueError):
                save_key(temp, "gateway", "private-test-key")

    def test_key_directory_symlink_cannot_escape_into_a_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            checkout = root / "repo"
            checkout.mkdir()
            (checkout / ".git").mkdir()
            private = root / "private"
            private.mkdir()
            (private / "keys").symlink_to(checkout, target_is_directory=True)
            with self.assertRaises(ValueError):
                save_key(private, "gateway", "private-test-key")
            self.assertFalse((checkout / "gateway.key").exists())

    def test_direct_api_requires_a_named_provider_and_explicit_model(self):
        for changes in [{"backend": "api"}, {"backend": "api", "model_provider": "gateway", "model": "auto"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_config(dict(DEFAULTS, **changes))

    def test_raw_api_keys_cannot_be_saved_in_public_configuration(self):
        cfg = dict(DEFAULTS, backend="api", model_provider="gateway", model="small-model",
                   model_providers={"gateway": {"base_url": "https://example.com/v1", "api_key": "must-not-save"}})
        with self.assertRaises(ValueError):
            validate_config(cfg)

    def test_chat_and_responses_provider_definitions_are_accepted(self):
        for wire in ["chat", "responses"]:
            cfg = dict(DEFAULTS, backend="api", model_provider="gateway", model="small-model",
                       model_providers={"gateway": {"base_url": "https://example.com/v1", "wire_api": wire,
                           "env_key": "EXAMPLE_PROVIDER_KEY"}})
            actual = validate_config(cfg)
            self.assertEqual(actual["model_providers"]["gateway"]["wire_api"], wire)

    def test_credentials_embedded_in_urls_are_rejected(self):
        for url in ["https://secret@example.com/v1", "https://example.com/v1?api_key=secret"]:
            cfg = dict(DEFAULTS, backend="api", model_provider="gateway", model="small-model",
                       model_providers={"gateway": {"base_url": url}})
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_config(cfg)

    def test_shell_model_selection_does_not_mutate_codex_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/semantic_status.py"),
                "--data-dir", temp, "configure", "--set", 'backend="api"', "--set", 'model_provider="gateway"',
                "--set", 'model="my-small-model"', "--set",
                'model_providers={"gateway":{"base_url":"https://example.com/v1","wire_api":"chat","env_key":"EXAMPLE_PROVIDER_KEY"}}'],
                capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads((pathlib.Path(temp) / "config.json").read_text())
            self.assertEqual(saved["model"], "my-small-model")
            self.assertEqual(saved["backend"], "api")
            self.assertEqual(saved["max_tokens_per_day"], 50000)


if __name__ == "__main__":
    unittest.main()
