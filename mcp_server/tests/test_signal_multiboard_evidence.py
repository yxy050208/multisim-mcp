"""Changed or incompletely hashed archives are rejected before replay."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


class SignalEvidenceArchiveTest(unittest.TestCase):
    def setUp(self):
        tool = Path(__file__).resolve().parents[2] / "tools" / "verify_signal_multiboard_evidence.py"
        spec = importlib.util.spec_from_file_location("signal_evidence_verifier_test", tool)
        self.verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.verifier)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "signal-acceptance.json").write_text("{}", encoding="utf-8")
        self.manifest = {"schema_version": 1, "files": {
            "signal-acceptance.json": hashlib.sha256(b"{}").hexdigest()}}

    def verify(self):
        (self.root / "sha256-manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        return self.verifier.verify(self.root)

    def test_changed_file_or_path_escape_is_rejected(self):
        (self.root / "signal-acceptance.json").write_text('{"status":"accepted"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.verify()
        self.manifest["files"]["../outside.txt"] = "0" * 64
        with self.assertRaises(ValueError):
            self.verify()

    def test_omitted_file_cannot_bypass_hash_check(self):
        self.manifest["files"].clear()
        with self.assertRaisesRegex(ValueError, "every archive file"):
            self.verify()
        self.manifest["files"] = {"signal-acceptance.json": hashlib.sha256(b"{}").hexdigest()}
        (self.root / "extra.txt").write_text("unhashed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "every archive file"):
            self.verify()
