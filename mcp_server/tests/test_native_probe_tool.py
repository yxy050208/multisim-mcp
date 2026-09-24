"""Pure validation tests for the isolated native replacement probe."""

import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_probe():
    path = Path(__file__).resolve().parents[2] / "tools" / "probe_native_replacement.py"
    spec = importlib.util.spec_from_file_location("native_probe_under_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load native probe tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NativeProbeValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.probe = _load_probe()

    def test_database_enum_is_the_master_database_value(self) -> None:
        self.assertEqual(self.probe.MASTER_DB, 0)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "carrier.ms14"
            source.write_bytes(b"not a real circuit")
            with self.assertRaisesRegex(ValueError, "master database"):
                self.probe.probe_replacement(
                    source,
                    Path(tmp) / "result.ms14",
                    component="U1",
                    section="",
                    database=1073741824,
                    group="Mixed",
                    family="TIMER",
                    source_name="LM555CN",
                    model="",
                )

    def test_safe_output_rejects_root_and_non_ms14_paths(self) -> None:
        with self.assertRaises(ValueError):
            self.probe._safe_output(Path("C:\\"))
        with self.assertRaises(ValueError):
            self.probe._safe_output(Path("result.xml"))

    def test_token_validation_rejects_shell_metacharacters(self) -> None:
        with self.assertRaises(ValueError):
            self.probe._validate_token("TIMER;Stop-Process", "family")
        self.assertEqual(
            self.probe._validate_token("", "section", allow_empty=True), ""
        )


if __name__ == "__main__":
    unittest.main()
