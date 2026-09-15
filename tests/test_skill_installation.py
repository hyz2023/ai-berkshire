"""Exercise the real installer in an isolated destination, never global skills."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class SkillInstallation(unittest.TestCase):
    def install(self, dest, *extra):
        return subprocess.run([sys.executable, str(ROOT / "scripts/install-codex-skills.py"),
                               "--dest", str(dest), "--skill", "earnings-review", *extra],
                              capture_output=True, text=True, encoding="utf-8")

    def test_installation_runs_tools_outside_checkout(self):
        with tempfile.TemporaryDirectory(prefix="berkshire install ") as temp:
            dest = Path(temp) / "skills"
            result = self.install(dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            runtime = dest / "earnings-review/references/runtime"
            result = subprocess.run([sys.executable, str(runtime / "tools/financial_rigor.py"),
                                     "calc", "--expr", "0.1 + 0.2"], cwd=temp,
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("0.3", result.stdout)
            self.assertTrue((runtime / "skills/financial-data.md").is_file())
            self.assertFalse((dest / "investment-team").exists())
            result = subprocess.run([sys.executable, str(runtime / "tools/report_audit.py"),
                                     "verdict", "--results", "[]", "--output-json"], cwd=temp,
                                    capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 1)
            self.assertIn('"INCOMPLETE"', result.stdout)
            self.assertNotIn("Traceback", result.stderr)

    def test_existing_skill_is_preserved_unless_replacement_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "skills"
            target = dest / "earnings-review"
            target.mkdir(parents=True)
            custom = target / "SKILL.md"
            custom.write_text("my customization", encoding="utf-8")
            result = self.install(dest)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(custom.read_text(encoding="utf-8"), "my customization")
            result = self.install(dest, "--replace")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotEqual(custom.read_text(encoding="utf-8"), "my customization")
            backups = list((dest / ".berkshire-backups").glob("*/earnings-review/SKILL.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_text(encoding="utf-8"), "my customization")

    def test_unknown_skill_does_not_write_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            dest = Path(temp) / "skills"
            result = self.install(dest, "--skill", "nonexistent")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(dest.exists())


if __name__ == "__main__":
    unittest.main()
