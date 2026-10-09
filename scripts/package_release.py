#!/usr/bin/env python3
"""Build a deterministic, allowlisted release ZIP with no local runtime records."""
import argparse
import json
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
RUNTIME_DIRS = [".agents", ".codex-plugin", ".claude-plugin", "hooks", "scripts", "skills", "tests", "docs"]
ROOT_FILES = ["package.json", ".claude-plugin.json", "README.md", "README.zh-CN.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md"]


def package(output_dir):
    metadata = json.loads((ROOT / "package.json").read_text())
    version = metadata["version"]
    for manifest in [".codex-plugin/plugin.json", ".claude-plugin/plugin.json"]:
        if json.loads((ROOT / manifest).read_text())["version"] != version:
            raise ValueError("manifest versions do not match package.json")
    files = [ROOT / f for f in ROOT_FILES]
    for name in RUNTIME_DIRS:
        files.extend(p for p in (ROOT / name).rglob("*") if p.is_file()
                     and "__pycache__" not in p.parts and p.suffix != ".pyc")
    for p in files:
        if p.is_symlink() or not p.exists() or not p.resolve().is_relative_to(ROOT):
            raise ValueError("invalid release path: " + str(p))
        if (p.name in ["auth.json", "config.json", "state.sqlite3", "handoff.md", ".env"]
                or p.suffix.lower() in [".log", ".sqlite3", ".key", ".pem"]
                or "keys" in p.relative_to(ROOT).parts):
            raise ValueError("runtime data in release: " + str(p))
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / ("codex-semantic-status-" + version + ".zip")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in sorted(set(files)):
            name = "codex-semantic-status/" + p.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(name, (2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            z.writestr(info, p.read_bytes())
    with zipfile.ZipFile(output) as z:
        if z.testzip() is not None:
            raise ValueError("invalid release archive")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=pathlib.Path, default=ROOT / "dist")
    args = parser.parse_args()
    print(package(args.output_dir.resolve()))
