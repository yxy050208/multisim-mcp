import unittest
from pathlib import Path

from multisim_mcp.hybrid_multiboard_evidence import verify_hybrid_multiboard_evidence


class HybridMultiboardEvidenceTest(unittest.TestCase):
    def test_missing_archive_is_fail_closed(self):
        with self.assertRaises(ValueError):
            verify_hybrid_multiboard_evidence(Path("missing-hybrid-archive"))


if __name__ == "__main__":
    unittest.main()
