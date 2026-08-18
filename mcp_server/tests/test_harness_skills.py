"""Tests for the packaged DeepSeek Harness skill bundle."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.harness_skills import (
    bundled_harness_skill_manifest,
    bundled_harness_skills,
    install_harness_skills,
)


EXPECTED_SKILLS = {
    "multisim-create-experiment",
    "multisim-debug-circuit",
    "multisim-compare-experiments",
    "multisim-write-lab-report",
    "multisim-verify-requirements",
}


class HarnessSkillBundleTest(unittest.TestCase):
    def test_manifest_and_frontmatter_define_exactly_five_skills(self) -> None:
        manifest = bundled_harness_skill_manifest()
        skills = bundled_harness_skills()
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(set(manifest["skills"]), EXPECTED_SKILLS)
        self.assertEqual(set(skills), EXPECTED_SKILLS)
        for name, content in skills.items():
            with self.subTest(skill=name):
                self.assertTrue(content.startswith("---\n"))
                self.assertIn(f"\nname: {name}\n", content)
                self.assertIn("description:", content)
                self.assertIn("Multisim MCP", content)

    def test_installer_refuses_overwrite_then_replaces_only_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / ".dsh" / "skills"
            first = install_harness_skills(str(output))
            self.assertEqual(first["skill_count"], 5)
            self.assertEqual(set(first["skills"]), EXPECTED_SKILLS)
            for name in EXPECTED_SKILLS:
                self.assertTrue((output / name / "SKILL.md").is_file())

            edited = output / "multisim-debug-circuit" / "SKILL.md"
            edited.write_text("user edit\n", encoding="utf-8")
            with self.assertRaisesRegex(FileExistsError, "--force"):
                install_harness_skills(str(output))
            self.assertEqual(edited.read_text(encoding="utf-8"), "user edit\n")

            replaced = install_harness_skills(str(output), force=True)
            self.assertTrue(replaced["force"])
            self.assertIn("name: multisim-debug-circuit", edited.read_text(encoding="utf-8"))

    def test_installer_rejects_empty_and_filesystem_root_outputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            install_harness_skills("  ")
        with self.assertRaisesRegex(ValueError, "filesystem root"):
            install_harness_skills(Path.cwd().anchor)

    def test_preflight_rejects_invalid_skill_directory_without_partial_install(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "skills"
            output.mkdir()
            blocked = output / "multisim-write-lab-report"
            blocked.write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must be a regular directory"):
                install_harness_skills(str(output))

            self.assertEqual(
                sorted(path.name for path in output.iterdir()),
                ["multisim-write-lab-report"],
            )


if __name__ == "__main__":
    unittest.main()
