from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.project_inspection import inspect_project
from multisim_mcp.workspace_manifest import write_directory_manifest


class ProjectInspectionTest(unittest.TestCase):
    def _manifest(
        self,
        root: Path,
        *,
        kind: str,
        entity_id: str,
        state: str = "succeeded",
        metadata: dict[str, object] | None = None,
    ) -> None:
        artifact = root / "summary.json"
        artifact.write_text('{"ok": true}\n', encoding="utf-8")
        write_directory_manifest(
            root,
            directory_kind=kind,
            entity_id=entity_id,
            state=state,
            artifacts={"summary.json": "summary"},
            metadata=metadata or {"backend": "fixture", "visible": True},
        )

    def test_returns_verified_root_and_nested_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifest(root, kind="project", entity_id="project-fixture", state="active")
            experiment = root / "experiments" / "exp-1"
            experiment.mkdir(parents=True)
            self._manifest(experiment, kind="experiment", entity_id="exp-1")
            optimization = root / "optimization"
            optimization.mkdir()
            self._manifest(
                optimization,
                kind="optimization",
                entity_id="opt-1",
                state="running",
            )

            snapshot = inspect_project(root)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertTrue(snapshot["root_manifest_present"])
        self.assertEqual(snapshot["summary"]["manifest_count"], 3)
        self.assertEqual(snapshot["summary"]["verified_count"], 3)
        self.assertEqual(
            snapshot["summary"]["kind_counts"],
            {"experiment": 1, "optimization": 1, "project": 1},
        )
        self.assertEqual(snapshot["root_manifest"]["metadata_keys"], ["backend", "visible"])
        self.assertEqual(snapshot["entries"][1]["path"], "experiments/exp-1")

    def test_invalid_child_is_reported_without_hiding_other_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifest(root, kind="project", entity_id="project-fixture")
            valid = root / "valid"
            valid.mkdir()
            self._manifest(valid, kind="experiment", entity_id="exp-valid")
            invalid = root / "broken"
            invalid.mkdir()
            (invalid / "directory.manifest.json").write_text("{not-json", encoding="utf-8")

            snapshot = inspect_project(root)

        self.assertEqual(snapshot["summary"]["manifest_count"], 3)
        self.assertEqual(snapshot["summary"]["invalid_count"], 1)
        self.assertFalse(snapshot["success"])
        broken = next(item for item in snapshot["entries"] if item["path"] == "broken")
        self.assertEqual(broken["integrity_status"], "invalid")
        self.assertEqual(broken["error"]["type"], "ValueError")

    def test_limits_and_no_verify_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifest(root, kind="project", entity_id="project-fixture")
            for index in range(3):
                child = root / f"child-{index}"
                child.mkdir()
                self._manifest(child, kind="experiment", entity_id=f"exp-{index}")

            snapshot = inspect_project(root, verify=False, max_entries=2, max_depth=1)

        self.assertTrue(snapshot["limits"]["truncated"])
        self.assertFalse(snapshot["limits"]["verification_enabled"])
        self.assertEqual(snapshot["summary"]["manifest_count"], 2)
        self.assertGreaterEqual(snapshot["summary"]["scanned_directory_count"], 2)
        self.assertTrue(
            all(item["integrity_status"] == "loaded-without-verification" for item in snapshot["entries"])
        )

    def test_exposes_validated_approval_provenance_without_raw_metadata(self) -> None:
        provenance = {
            "schema_version": 1,
            "kind": "multisim-mcp-approved-simulation-provenance",
            "simulation_plan_approval_id": "simulation-approval-fixture",
            "simulation_plan_approval_digest": "a" * 64,
            "netlist_approval_id": "netlist-approval-fixture",
            "netlist_approval_digest": "b" * 64,
            "compiled_id": "compiled-fixture",
            "compiled_digest": "c" * 64,
            "design_id": "design-fixture",
            "design_digest": "d" * 64,
            "spice_sha256": "e" * 64,
            "spec_digest": "f" * 64,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._manifest(root, kind="project", entity_id="project-fixture")
            experiment = root / "experiment"
            experiment.mkdir()
            self._manifest(
                experiment,
                kind="experiment",
                entity_id="exp-approved",
                metadata={"approval_provenance": provenance},
            )
            snapshot = inspect_project(root)

        entry = next(item for item in snapshot["entries"] if item["path"] == "experiment")
        self.assertEqual(entry["approval_provenance_status"], "verified")
        self.assertEqual(entry["approval_provenance"], provenance)
        self.assertNotIn("experiment_spec", entry["approval_provenance"])


if __name__ == "__main__":
    unittest.main()
