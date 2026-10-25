import unittest

from multisim_mcp.component_compat import (
    build_component_mapping_matrix,
    resolve_component_mapping,
    resolve_connector_mapping,
    validate_component_manifest,
    require_verified_mappings,
    load_manifest_for_version,
)


class ComponentCompatibilityTest(unittest.TestCase):
    def test_installed_manifest_matches_reviewed_source(self):
        from importlib.resources import files
        from pathlib import Path
        import json

        resource = files('multisim_mcp').joinpath('compatibility/components-14.3.json')
        source = Path(__file__).resolve().parents[2] / 'compatibility/components-14.3.json'
        self.assertEqual(json.loads(resource.read_text(encoding='utf-8')),
                         json.loads(source.read_text(encoding='utf-8')))

    def setUp(self):
        self.manifest = {"schema_version": 1, "multisim_version": "14.3", "components": [
            {"logical_family": "opamp", "native_name": "OPAMP5", "model_source": "vendor",
             "pin_signature": ["in+", "in-", "v+", "v-", "out"], "supported_versions": ["14.3"], "verified": True},
            {"logical_family": "opamp", "native_name": "IDEALOPAMP", "model_source": "fallback",
             "pin_signature": ["in+", "in-", "v+", "v-", "out"], "supported_versions": ["14.2"], "verified": True},
        ]}

    def test_selects_version_specific_mapping(self):
        result = resolve_component_mapping(self.manifest, "opamp", "14.2", ["in+", "in-", "v+", "v-", "out"])
        self.assertEqual(result["mapping"]["native_name"], "IDEALOPAMP")
        self.assertEqual(result["status"], "native-verified")

    def test_unsupported_version_is_unavailable(self):
        result = resolve_component_mapping(self.manifest, "opamp", "13.0")
        self.assertEqual(result["status"], "unavailable")

    def test_invalid_version_list_rejected(self):
        with self.assertRaises(ValueError):
            validate_component_manifest({"schema_version": 1, "components": [{
                "logical_family": "r", "native_name": "R", "model_source": "x",
                "pin_signature": ["1", "2"], "supported_versions": "14.3"}]})

    def test_manifest_rejects_empty_or_wrongly_typed_identity_fields(self):
        base = {
            "schema_version": 1,
            "components": [{
                "logical_family": "r",
                "native_name": "R",
                "model_source": "local",
                "pin_signature": ["1", "2"],
            }],
        }
        invalid = (
            ("logical_family", ""),
            ("native_name", 7),
            ("model_source", "  "),
            ("pin_signature", ["1", None]),
            ("supported_versions", ["14.3", 14.2]),
            ("verified", "false"),
        )
        for field, value in invalid:
            with self.subTest(field=field):
                manifest = {**base, "components": [dict(base["components"][0])]}
                manifest["components"][0][field] = value
                with self.assertRaises(ValueError):
                    validate_component_manifest(manifest)

    def test_verified_set_is_required(self):
        result = require_verified_mappings(self.manifest, "14.3", {"opamp": ["in+", "in-", "v+", "v-", "out"]})
        self.assertEqual(result["mappings"]["opamp"]["mapping"]["native_name"], "OPAMP5")
        with self.assertRaises(ValueError):
            require_verified_mappings(self.manifest, "14.3", {"vendor-opamp5-virtual": ["in+", "in-", "v+", "v-", "out"]})

    def test_version_manifest_selection_fails_closed(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "compatibility"
        self.assertEqual(load_manifest_for_version(root, "14.3")["multisim_version"], "14.3")
        with self.assertRaises(ValueError):
            load_manifest_for_version(root, "13.0")

    def test_14_3_manifest_covers_verified_digital_contracts(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "compatibility"
        manifest = load_manifest_for_version(root, "14.3")
        required = {
            "digital-not": ["I1", "O1", "VDD", "VSS"],
            "digital-and2": ["1A", "1B", "1Y", "VDD", "VSS"],
            "digital-or2": ["1A", "1B", "1Y", "VDD", "VSS"],
            "digital-jk": ["J", "K", "CLK", "SET", "RESET", "Q", "~Q"],
        }
        result = require_verified_mappings(manifest, "14.3", required)
        self.assertEqual(set(result["mappings"]), set(required))

    def test_14_3_manifest_covers_lm324_section_a_contract(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "compatibility"
        manifest = load_manifest_for_version(root, "14.3")
        result = require_verified_mappings(
            manifest,
            "14.3",
            {"lm324aj-section-a": ["1IN+", "1IN-", "VS+", "VS-", "1OUT"]},
        )
        mapping = result["mappings"]["lm324aj-section-a"]["mapping"]
        self.assertEqual(mapping["native_name"], "LM324AJ")
        self.assertEqual(mapping["model_source"], "licensed-local-LM158_4")
        self.assertEqual(mapping["supported_versions"], ["14.3"])
        self.assertTrue(mapping["verified"])

        with self.assertRaises(ValueError):
            require_verified_mappings(
                manifest,
                "14.2",
                {"lm324aj-section-a": ["1IN+", "1IN-", "VS+", "VS-", "1OUT"]},
            )

    def test_digital_mapping_fails_closed_for_unverified_version(self):
        from pathlib import Path
        root = Path(__file__).resolve().parents[2] / "compatibility"
        with self.assertRaises(ValueError):
            require_verified_mappings(
                load_manifest_for_version(root, "14.3"),
                "14.4",
                {"digital-jk": ["J", "K", "CLK", "SET", "RESET", "Q", "~Q"]},
            )

    def test_repo_manifest_exposes_verified_hdr1x4_mapping(self):
        from pathlib import Path

        manifest = load_manifest_for_version(
            Path(__file__).resolve().parents[2] / "compatibility", "14.3"
        )
        connector = {
            "part": "HDR1X4",
            "pins": [{"number": 1}, {"number": 2}, {"number": 3}, {"number": 4}],
        }
        result = resolve_connector_mapping(manifest, connector, "14.3")
        self.assertEqual(result["status"], "native-verified")
        self.assertEqual(result["mapping"]["native_name"], "HDR1X4")
        connector["pins"][-1]["number"] = 5
        self.assertEqual(
            resolve_connector_mapping(manifest, connector, "14.3")["status"],
            "unavailable",
        )

    def test_connector_mapping_requires_exact_part_and_pin_signature(self):
        manifest = {
            "schema_version": 1,
            "multisim_version": "14.3",
            "components": [{
                "logical_family": "connector:HEADER_1X2",
                "native_name": "HEADER_1X2",
                "part_number": "HEADER_1X2",
                "model_source": "user-local-native-template",
                "pin_signature": ["1", "2"],
                "supported_versions": ["14.3"],
                "verified": True,
            }],
        }
        connector = {"part": "HEADER_1X2", "pins": [{"number": 1}, {"number": 2}]}
        result = resolve_connector_mapping(manifest, connector, "14.3")
        self.assertEqual(result["status"], "native-verified")
        self.assertEqual(result["mapping"]["part_number"], "HEADER_1X2")
        connector["pins"][1]["number"] = 3
        self.assertEqual(resolve_connector_mapping(manifest, connector, "14.3")["status"], "unavailable")

    def test_component_mapping_matrix_never_borrows_a_neighboring_version(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2] / "compatibility"
        matrix = build_component_mapping_matrix(
            root, ["14.3", "14.2", "14.3.1", "future-release", "14.3"]
        )
        self.assertEqual(matrix["available_manifest_versions"], ["14.3"])
        self.assertEqual(matrix["exact_manifest_versions"], ["14.3"])
        self.assertEqual(matrix["manifest_unavailable_versions"], ["14.2", "14.3.1"])
        self.assertEqual(matrix["unsupported_versions"], ["future-release"])
        exact = matrix["entries"][0]
        self.assertEqual(exact["status"], "exact-manifest")
        self.assertEqual(exact["manifest"]["multisim_version"], "14.3")
        self.assertEqual(exact["manifest"]["component_count"], len(exact["mappings"]))
        self.assertTrue(any(item["logical_family"] == "connector:HDR1X4" for item in exact["mappings"]))
        missing = next(item for item in matrix["entries"] if item["requested_version"] == "14.2")
        self.assertEqual(missing["mappings"], [])
