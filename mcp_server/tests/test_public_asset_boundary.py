"""Public-release guards inspect staged paths and actual distribution members."""

from __future__ import annotations

import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, TOOLS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


audit = _load("public_boundary_release_audit", "release_audit.py")
package_check = _load("public_boundary_package_check", "check_mcp_package.py")


def _distributions(root: Path, *, wheel_extra: str = "", sdist_extra: str = "") -> None:
    with zipfile.ZipFile(root / "multisim_mcp-0.0.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("multisim_mcp/core.py", "# public source\n")
        wheel.writestr("multisim_mcp-0.0.0.dist-info/licenses/LICENSE", "MIT\n")
        for index in range(5):
            wheel.writestr(f"multisim_mcp/harness_skills/skill{index}/SKILL.md", "Public skill\n")
        if wheel_extra:
            wheel.writestr(wheel_extra, "private content must not be inspected or published")
    with tarfile.open(root / "multisim_mcp-0.0.0.tar.gz", "w:gz") as sdist:
        names = ["LICENSE", "multisim_mcp/core.py"]
        names += [f"multisim_mcp/harness_skills/skill{index}/SKILL.md" for index in range(5)]
        if sdist_extra:
            names.append(sdist_extra)
        for name in names:
            content = b"public source\n"
            member = tarfile.TarInfo(f"multisim_mcp-0.0.0/{name}")
            member.size = len(content)
            sdist.addfile(member, io.BytesIO(content))


class PublicAssetBoundaryTest(unittest.TestCase):
    def test_private_containers_blocked_and_public_history_allowed(self):
        for path in (
            "commercial/optimizer.py", "mcp_server/multisim_mcp/PAID/engine.py",
            "integrations/private/prompt.txt", "commercial_assets/models/lib.txt",
            "proprietary/evaluation.json", "agensi-products/free/README.md",
            "release/free.zip", "paid-bundle.7z", "bundle.RAR", "bundle.tar.gz",
            "commercial/", "mcp_server\\private\\model.py",
        ):
            with self.subTest(path=path):
                self.assertTrue(audit.is_forbidden_asset(path))
        for path in (
            "docs/OPEN_SOURCE_SCOPE.md", "docs/MULTIBOARD_DIGITAL_REGRESSION.md",
            "mcp_server/multisim_mcp/multiboard_planning.py",
            "mcp_server/multisim_mcp/layout_validation.py", "examples/public.cir",
        ):
            with self.subTest(path=path):
                self.assertFalse(audit.is_forbidden_asset(path))

    def test_force_staged_private_file_cannot_bypass_gitignore(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".gitignore").write_text("private/\n", encoding="utf-8")
            (root / "private").mkdir()
            (root / "private" / "customer.txt").write_text("local data", encoding="utf-8")
            chinese_path = "private/客户资料.txt"
            (root / chinese_path).write_text("local data", encoding="utf-8")
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            with patch.object(audit, "ROOT", root):
                self.assertNotIn("private/customer.txt", audit.candidate_files())
                self.assertNotIn(chinese_path, audit.candidate_files())
                subprocess.run(
                    ["git", "add", "--force", "private/customer.txt", chinese_path],
                    cwd=root, check=True, capture_output=True,
                )
                self.assertIn("private/customer.txt", audit.candidate_files())
                self.assertIn(chinese_path, audit.candidate_files())
                read_text = Path.read_text

                def public_text_only(path, *args, **kwargs):
                    self.assertNotEqual(path.parent, root / "private")
                    return read_text(path, *args, **kwargs)

                with patch.object(audit, "PYPROJECT", root / "missing.toml"), patch.object(
                    Path, "read_text", public_text_only,
                ):
                    findings = audit.audit()
                self.assertIn(
                    "private/customer.txt",
                    [finding.path for finding in findings if finding.check == "forbidden-asset"],
                )
                self.assertIn(
                    chinese_path,
                    [finding.path for finding in findings if finding.check == "forbidden-asset"],
                )

    def test_public_code_only_distributions_are_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _distributions(root)
            wheel, sdist = package_check.verify(root, "0.0.0")
            self.assertTrue(wheel.is_file())
            self.assertTrue(sdist.is_file())

    def test_wheel_rejects_private_module_and_nested_product_archive(self):
        for name in (
            "multisim_mcp/commercial/engine.py", "multisim_mcp/data/free.zip",
            "multisim_mcp/templates/r_element.xml",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _distributions(root, wheel_extra=name)
                with self.assertRaisesRegex(ValueError, "wheel contains a forbidden public asset"):
                    package_check.verify(root, "0.0.0")

    def test_sdist_rejects_private_module_and_nested_product_archive(self):
        for name in (
            "multisim_mcp/private/customer.txt", "product.tgz",
            "multisim_mcp/templates/model.xml",
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _distributions(root, sdist_extra=name)
                with self.assertRaisesRegex(ValueError, "sdist contains a forbidden public asset"):
                    package_check.verify(root, "0.0.0")


if __name__ == "__main__":
    unittest.main()
