import unittest

from multisim_mcp.circuit_template_registry import get_template_family, list_template_families


class CircuitTemplateRegistryTest(unittest.TestCase):
    def test_catalog_has_eight_families_and_unique_ids(self):
        items = list_template_families()
        self.assertEqual(len(items), 8)
        self.assertEqual(len({item["id"] for item in items}), 8)
        self.assertEqual(get_template_family("dc_network")["status"], "next")

    def test_unknown_family_is_rejected(self):
        with self.assertRaises(KeyError):
            get_template_family("unknown")


if __name__ == "__main__":
    unittest.main()
