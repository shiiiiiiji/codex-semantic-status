"""A release must reject misplaced credentials instead of silently shipping them."""
import json
import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import package_release


class PackagingTests(unittest.TestCase):
    def test_misplaced_key_file_blocks_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp).resolve()
            for folder in package_release.RUNTIME_DIRS:
                (root / folder).mkdir(parents=True)
            for filename in package_release.ROOT_FILES:
                (root / filename).write_text("")
            for filename in ["package.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json"]:
                (root / filename).write_text(json.dumps({"version": "1.2.0"}))
            (root / "scripts/gateway.key").write_text("private-test-key")
            with patch.object(package_release, "ROOT", root), self.assertRaises(ValueError):
                package_release.package(root / "dist")
            self.assertFalse((root / "dist/codex-semantic-status-1.2.0.zip").exists())
