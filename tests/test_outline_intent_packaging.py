import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_outline_intent_runtime import VALID_INTENT

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("outline-intent-sync", "outline-intent-review", "outline-intent-revise")


class OutlineIntentPackagingTests(unittest.TestCase):
    def test_zcode_manifests_match_marketplace_and_expose_packaged_skills(self):
        catalog = json.loads((ROOT / "marketplace.json").read_text())
        for name in NAMES:
            with self.subTest(name=name):
                entry = next(x for x in catalog["plugins"] if x["name"] == name)
                base = (ROOT / entry["source"]).resolve()
                manifest = json.loads((base / ".zcode-plugin/plugin.json").read_text())
                self.assertEqual(manifest["name"], name)
                self.assertEqual(manifest["version"], entry["version"])
                self.assertEqual(manifest["description"], entry["description"])
                self.assertTrue((base / manifest["skills"] / name / "SKILL.md").is_file())
                for host in (".codex-plugin", ".claude-plugin"):
                    other = json.loads((base / host / "plugin.json").read_text())
                    self.assertEqual(other["version"], entry["version"])

    def test_three_plugins_are_independently_registered_and_self_contained(self):
        for name in NAMES:
            with self.subTest(name=name):
                base = ROOT / "plugins" / name
                self.assertTrue(base.is_dir(), f"{name} plugin is missing")
                for host in (".codex-plugin", ".claude-plugin"):
                    manifest = base / host / "plugin.json"
                    self.assertTrue(
                        manifest.is_file(), f"{name} host manifest is missing"
                    )
                    data = json.loads(manifest.read_text())
                    self.assertEqual(data["name"], name)
                    self.assertEqual(data["skills"], "./skills/")
                    self.assertNotIn("mcpServers", data)
                skill = base / "skills" / name
                for path in (
                    "SKILL.md",
                    "agents/openai.yaml",
                    "assets/intent-template.md",
                    "references/workflow-contract.md",
                    "references/runtime-usage.md",
                    "scripts/outline_intent.py",
                    "scripts/plan_engine.py",
                    "scripts/comment_mirror.py",
                    "bundle.json",
                ):
                    self.assertTrue((skill / path).is_file(), f"{name}/{path} missing")
                with tempfile.TemporaryDirectory() as tmp:
                    isolated = Path(tmp) / name
                    shutil.copytree(base, isolated)
                    script = isolated / "skills" / name / "scripts/outline_intent.py"
                    result = subprocess.run(
                        [sys.executable, str(script), "--help"],
                        cwd=tmp,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("preflight", result.stdout)
                    fixture = Path(tmp) / "intent.md"
                    fixture.write_text(VALID_INTENT, encoding="utf-8")
                    validated = subprocess.run(
                        [
                            sys.executable,
                            str(script),
                            "validate",
                            "--file",
                            str(fixture),
                        ],
                        cwd=tmp,
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(
                        validated.returncode, 0, validated.stdout + validated.stderr
                    )

    def test_all_marketplaces_include_each_plugin_exactly_once(self):
        for path in (
            "marketplace.json",
            ".agents/plugins/marketplace.json",
            ".claude-plugin/marketplace.json",
        ):
            data = json.loads((ROOT / path).read_text())
            for name in NAMES:
                entries = [x for x in data["plugins"] if x["name"] == name]
                self.assertEqual(len(entries), 1, f"{path}: {name}")

    def test_bundle_manifest_verifies_shipped_bytes_and_no_machine_dependency(self):
        canonical = ROOT / "tools/outline_intent"
        for name in NAMES:
            skill = ROOT / "plugins" / name / "skills" / name
            self.assertTrue((skill / "bundle.json").exists(), "bundle manifest missing")
            bundle = json.loads((skill / "bundle.json").read_text())
            for relative, expected in bundle["sha256"].items():
                self.assertEqual(
                    hashlib.sha256((skill / relative).read_bytes()).hexdigest(),
                    expected,
                )
            for source, shipped in [
                ("outline_intent.py", "scripts/outline_intent.py"),
                ("plan_engine.py", "scripts/plan_engine.py"),
                ("comment_mirror.py", "scripts/comment_mirror.py"),
                ("intent-template.md", "assets/intent-template.md"),
                ("workflow-contract.md", "references/workflow-contract.md"),
            ]:
                self.assertEqual(
                    (canonical / source).read_bytes(), (skill / shipped).read_bytes()
                )
            for f in skill.rglob("*"):
                if f.is_file() and f.suffix in (".py", ".md", ".json", ".yaml"):
                    self.assertNotIn("/Users/louis/", f.read_text(), str(f))

    def test_regeneration_check_detects_no_drift(self):
        script = ROOT / "tools/bundle_outline_intent.py"
        self.assertTrue(script.exists(), "bundle generation/check command missing")
        result = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
