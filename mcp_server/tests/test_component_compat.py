import unittest

from multisim_mcp.component_compat import resolve_component_mapping, validate_component_manifest, require_verified_mappings, load_manifest_for_version


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
