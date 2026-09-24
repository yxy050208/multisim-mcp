"""Regression tests for codec output-path handling.

``decode`` runs the external decoder, which writes its XML beside the *source*
file, and then copies it to the caller's target. When the caller asked for a
nested destination the copy failed with a bare ``WinError 3`` because the
directory was never created. These tests pin the fixed behaviour without
needing the real decoder.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.multisim_client import Ms14Codec


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class DecodeTargetDirectoryTest(unittest.TestCase):
    def test_decode_creates_nested_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "design.ms14"
            source.write_bytes(b"fixture")
            decoder_output = root / "design.ms14.xml"
            decoder_output.write_text(
                "<?xml version='1.0'?><Item/>", encoding="utf-8"
            )
            target = root / "nested" / "deeper" / "out.xml"

            def fake_run(cmd, **kwargs):  # noqa: ANN001, ARG001
                return _FakeCompleted()

            with (
                patch.object(Ms14Codec, "_base_cmd", return_value=["ewd"]),
                patch(
                    "multisim_mcp.multisim_client.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = Ms14Codec().decode(str(source), str(target))

            self.assertTrue(target.is_file())
            self.assertEqual(result["xml"], str(target))
            self.assertGreater(result["size"], 0)

    def test_decode_without_target_returns_adjacent_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "design.ms14"
            source.write_bytes(b"fixture")
            (root / "design.ms14.xml").write_text(
                "<?xml version='1.0'?><Item/>", encoding="utf-8"
            )

            with (
                patch.object(Ms14Codec, "_base_cmd", return_value=["ewd"]),
                patch(
                    "multisim_mcp.multisim_client.subprocess.run",
                    return_value=_FakeCompleted(),
                ),
            ):
                result = Ms14Codec().decode(str(source))

            self.assertEqual(result["xml"], str(source) + ".xml")

    def test_decode_surfaces_decoder_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "design.ms14"
            source.write_bytes(b"fixture")
            with (
                patch.object(Ms14Codec, "_base_cmd", return_value=["ewd"]),
                patch(
                    "multisim_mcp.multisim_client.subprocess.run",
                    return_value=_FakeCompleted(1, stderr="boom"),
                ),
            ):
                with self.assertRaises(RuntimeError):
                    Ms14Codec().decode(str(source))


class EncodeTargetDirectoryTest(unittest.TestCase):
    def test_encode_creates_nested_target_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            xml = root / "design.xml"
            xml.write_text("<?xml version='1.0'?><Item/>", encoding="utf-8")
            target = root / "nested" / "deeper" / "out.ms14"

            def fake_run(cmd, **kwargs):  # noqa: ANN001, ARG001
                # Emulate the encoder writing to its --output argument.
                index = cmd.index("--output")
                Path(cmd[index + 1]).write_bytes(b"encoded")
                return _FakeCompleted()

            with (
                patch.object(Ms14Codec, "_base_cmd", return_value=["ewe"]),
                patch(
                    "multisim_mcp.multisim_client.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = Ms14Codec().encode(str(xml), str(target))

            self.assertTrue(target.is_file())
            self.assertEqual(result["ms14"], str(target))
            self.assertGreater(result["size"], 0)


if __name__ == "__main__":
    unittest.main()
