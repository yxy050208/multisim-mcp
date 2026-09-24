from __future__ import annotations

import unittest

from multisim_mcp.eda_core import (
    Artifact,
    ArtifactSet,
    CircuitComponent,
    CircuitDesign,
    DesignPatch,
    ModelReference,
    PatchOperation,
)


class EdaCoreTest(unittest.TestCase):
    def test_circuit_design_round_trip_derives_nets_and_freezes_metadata(self) -> None:
        design = CircuitDesign(
            design_id="filter-v1",
            title="RC low-pass",
            components=(
                CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                CircuitComponent("C1", "C", ("out", "0"), value="10n"),
            ),
            parameters={"corner_hz": 15915.49, "series": ["E24", 1]},
            annotations={"owner": "test"},
            model_references=(
                ModelReference(
                    name="passive-defaults",
                    source="inline",
                    sha256="a" * 64,
                    license="CC0-1.0",
                ),
            ),
        )

        self.assertEqual(design.nets, ("in", "out", "0"))
        self.assertEqual(design.parameters["series"], ("E24", 1))
        with self.assertRaises(TypeError):
            design.parameters["corner_hz"] = 1  # type: ignore[index]
        encoded = design.to_dict()
        self.assertEqual(encoded["schema_version"], 1)
        self.assertEqual(encoded["parameters"]["series"], ["E24", 1])
        self.assertEqual(CircuitDesign.from_dict(encoded).to_dict(), encoded)

    def test_circuit_design_rejects_ambiguous_or_unversioned_state(self) -> None:
        component = CircuitComponent("R1", "R", ("a", "0"), value="1k")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            CircuitComponent("R2", "R", (), value="1k")
        self.assertEqual(CircuitComponent("K1", "K", (), value="0.9").nodes, ())
        with self.assertRaisesRegex(ValueError, "duplicate component"):
            CircuitDesign(
                design_id="duplicate",
                title="Duplicate",
                components=(component, component),
            )
        with self.assertRaisesRegex(ValueError, "missing from"):
            CircuitDesign(
                design_id="missing-net",
                title="Missing net",
                components=(component,),
                nets=("a",),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            CircuitDesign(
                design_id="bad-number",
                title="Bad number",
                source_netlist="R1 a 0 1k",
                parameters={"value": float("nan")},
            )
        with self.assertRaisesRegex(ValueError, "schema_version"):
            CircuitDesign.from_dict(
                {
                    "schema_version": 2,
                    "design_id": "future",
                    "title": "Future",
                    "source_netlist": "R1 a 0 1k",
                }
            )
        payload = CircuitDesign(
            design_id="strict",
            title="Strict",
            source_netlist="R1 a 0 1k",
        ).to_dict()
        payload["transport_context"] = "mcp"
        with self.assertRaisesRegex(ValueError, "unknown fields"):
            CircuitDesign.from_dict(payload)

    def test_design_patch_has_bounded_operations_and_explicit_inverse(self) -> None:
        patch = DesignPatch(
            patch_id="patch-001",
            design_id="filter-v1",
            base_revision=3,
            description="Move R1 to the nearest E24 value",
            operations=(
                PatchOperation(
                    operation="set_component_value",
                    target="R1.value",
                    before="1030",
                    after="1k",
                    reason="Use an available E24 value",
                ),
                PatchOperation(
                    operation="set_annotation",
                    target="optimization.status",
                    before=None,
                    after="candidate",
                    reason="Record that the value is not approved yet",
                ),
            ),
        )

        inverse = patch.inverse()
        self.assertEqual(inverse.base_revision, 4)
        self.assertEqual(inverse.metadata["reverts_patch_id"], patch.patch_id)
        self.assertEqual(inverse.operations[0].target, "optimization.status")
        self.assertEqual(inverse.operations[-1].before, "1k")
        self.assertEqual(inverse.operations[-1].after, "1030")
        self.assertEqual(DesignPatch.from_dict(patch.to_dict()).to_dict(), patch.to_dict())
        long_patch = DesignPatch(
            patch_id="p" * 128,
            design_id="filter-v1",
            base_revision=0,
            description="Long identifier",
            operations=patch.operations[:1],
        )
        self.assertLessEqual(len(long_patch.inverse().patch_id), 128)
        with self.assertRaisesRegex(ValueError, "unsupported patch"):
            PatchOperation("replace_topology", "all", "a", "b", "Too broad")
        with self.assertRaisesRegex(ValueError, "must change"):
            PatchOperation("set_parameter", "gain", 1, 1, "No change")

    def test_topology_patch_operations_have_semantic_inverses(self) -> None:
        component = {
            "refdes": "R2",
            "kind": "R",
            "nodes": ["out", "0"],
            "value": "10k",
            "model": None,
            "parameters": {},
            "annotations": {},
        }
        add_component = PatchOperation(
            "add_component", "R2", None, component, "Add a load"
        )
        remove_component = add_component.inverse()
        self.assertEqual(remove_component.operation, "remove_component")
        self.assertEqual(remove_component.before["refdes"], "R2")
        self.assertIsNone(remove_component.after)

        add_net = PatchOperation("add_net", "sense", None, "sense", "Add net")
        remove_net = add_net.inverse()
        self.assertEqual(remove_net.operation, "remove_net")
        self.assertEqual(remove_net.before, "sense")
        self.assertIsNone(remove_net.after)

    def test_artifact_set_round_trip_rejects_duplicate_names(self) -> None:
        artifact = Artifact(
            artifact_id="filter-v1:simulate:1",
            name="result.raw",
            kind="simulation-data",
            location="multisim://experiments/demo/raw",
            media_type="application/octet-stream",
            size=100,
            sha256="b" * 64,
            metadata={"authoritative": True},
        )
        artifacts = ArtifactSet(
            artifact_set_id="filter-v1:simulate:run1",
            design_id="filter-v1",
            producer="multisim",
            artifacts=(artifact,),
        )
        encoded = artifacts.to_dict()
        self.assertEqual(ArtifactSet.from_dict(encoded).to_dict(), encoded)
        duplicate = Artifact(
            artifact_id="filter-v1:simulate:2",
            name="RESULT.RAW",
            kind="simulation-data",
            location="C:/result.raw",
            media_type="application/octet-stream",
            size=100,
            sha256="c" * 64,
        )
        with self.assertRaisesRegex(ValueError, "duplicate artifact names"):
            ArtifactSet(
                artifact_set_id="filter-v1:simulate:run2",
                design_id="filter-v1",
                producer="multisim",
                artifacts=(artifact, duplicate),
            )


if __name__ == "__main__":
    unittest.main()
