"""COM-free tests for versioned persistent workspace manifests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from multisim_mcp import __version__
from multisim_mcp.workspace_manifest import (
    DIRECTORY_MANIFEST_NAME,
    read_directory_manifest,
    verify_directory_manifest,
    write_directory_manifest,
)


class DirectoryManifestTest(unittest.TestCase):
    def test_round_trip_for_every_directory_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for kind in (
                "project",
                "experiment",
                "optimization",
                "global-optimization",
                "autonomous-correction",
                "benchmark-suite",
                "comparison",
                "patch-evaluation",
            ):
                with self.subTest(kind=kind):
                    root = base / kind
                    nested = root / "artifacts"
                    nested.mkdir(parents=True)
                    (root / "design.json").write_text("{}\n", encoding="utf-8")
                    (nested / "data.csv").write_text("x,y\n0,1\n", encoding="utf-8")

                    written = write_directory_manifest(
                        root,
                        directory_kind=kind,
                        entity_id=f"{kind}-fixture",
                        state="succeeded",
                        artifacts={
                            "design.json": "design",
                            "artifacts/data.csv": "simulation-data",
                        },
                        metadata={"backend": "fixture", "score": 1.0},
                    )
                    loaded = read_directory_manifest(root)

                    self.assertEqual(loaded, written)
                    self.assertEqual(loaded.schema_version, 1)
                    self.assertEqual(loaded.directory_kind, kind)
                    self.assertEqual(loaded.producer_version, __version__)
                    self.assertEqual(
                        [item.path for item in loaded.artifacts],
                        ["artifacts/data.csv", "design.json"],
                    )
                    self.assertTrue(
                        (root / DIRECTORY_MANIFEST_NAME)
                        .read_text(encoding="utf-8")
                        .endswith("\n")
                    )

    def test_rewrite_increments_revision_and_preserves_creation_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "design.json"
            artifact.write_text('{"revision": 0}\n', encoding="utf-8")
            first = write_directory_manifest(
                root,
                directory_kind="project",
                entity_id="project-revision",
                state="active",
                artifacts={"design.json": "design"},
            )
            artifact.write_text('{"revision": 1}\n', encoding="utf-8")
            second = write_directory_manifest(
                root,
                directory_kind="project",
                entity_id="project-revision",
                state="active",
                artifacts={"design.json": "design"},
            )

            self.assertEqual(first.revision, 0)
            self.assertEqual(second.revision, 1)
            self.assertEqual(second.created_at, first.created_at)
            self.assertEqual(second.manifest_id, first.manifest_id)
            self.assertNotEqual(second.artifacts[0].sha256, first.artifacts[0].sha256)

    def test_verification_detects_same_size_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = root / "result.txt"
            artifact.write_text("first", encoding="utf-8")
            write_directory_manifest(
                root,
                directory_kind="experiment",
                entity_id="experiment-tamper",
                state="succeeded",
                artifacts={"result.txt": "result"},
            )
            artifact.write_text("other", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                read_directory_manifest(root)
            loaded = read_directory_manifest(root, verify=False)
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_directory_manifest(root, loaded)

    def test_rejects_path_traversal_and_symlink_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            root = Path(tmp)
            outside_file = Path(outside) / "outside.txt"
            outside_file.write_text("outside", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "contained relative path"):
                write_directory_manifest(
                    root,
                    directory_kind="project",
                    entity_id="project-path",
                    state="active",
                    artifacts={"../outside.txt": "artifact"},
                )
            link = root / "linked.txt"
            try:
                link.symlink_to(outside_file)
            except (OSError, NotImplementedError):
                return
            with self.assertRaisesRegex(ValueError, "must not traverse symlinks"):
                write_directory_manifest(
                    root,
                    directory_kind="project",
                    entity_id="project-link",
                    state="active",
                    artifacts={"linked.txt": "artifact"},
                )

    def test_reader_rejects_unknown_fields_and_wrong_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "result.txt").write_text("ok", encoding="utf-8")
            write_directory_manifest(
                root,
                directory_kind="optimization",
                entity_id="optimization-schema",
                state="succeeded",
                artifacts={"result.txt": "result"},
            )
            path = root / DIRECTORY_MANIFEST_NAME
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown fields"):
                read_directory_manifest(root)

            payload.pop("unexpected")
            payload["schema_version"] = 999
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema_version must be 1"):
                read_directory_manifest(root)


if __name__ == "__main__":
    unittest.main()
