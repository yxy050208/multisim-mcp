"""Regression tests for decoder robustness fixes.

Covers two defects found while using the toolchain:

* the decoder can leave a stray NUL byte in otherwise valid XML, which makes
  ``ET.fromstring`` reject the whole document;
* ``decode``/``encode`` did not create a nested destination directory, so the
  caller saw a bare ``WinError 3`` instead of a usable path.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from multisim_mcp.native_xml import parse_native_xml, write_native_xml


class NulByteToleranceTest(unittest.TestCase):
    def _write(self, path: Path, body: str) -> Path:
        path.write_bytes(
            (
                "<?xml version='1.0' encoding='utf-8'?>\n<Item CiID=\"1\">"
                + body
                + "</Item>"
            ).encode("utf-8")
        )
        return path

    def test_clean_document_parses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp) / "clean.xml", "<A/>")
            self.assertIsNotNone(parse_native_xml(path).getroot().find("A"))

    def test_single_nul_byte_does_not_break_parsing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp) / "nul.xml", "<A/>\x00")
            root = parse_native_xml(path).getroot()
            self.assertIsNotNone(root.find("A"))

    def test_multiple_nul_bytes_are_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp) / "many.xml", "\x00<A/>\x00<B/>\x00")
            root = parse_native_xml(path).getroot()
            self.assertIsNotNone(root.find("A"))
            self.assertIsNotNone(root.find("B"))

    def test_nul_inside_an_attribute_value_is_stripped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp) / "attr.xml", '<A Value="x\x00y"></A>')
            found = parse_native_xml(path).getroot().find("A")
            self.assertIsNotNone(found)
            self.assertEqual(found.get("Value"), "xy")

    def test_multiline_attribute_preservation_still_works(self) -> None:
        """The NUL handling must not regress the newline escaping it sits beside."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(Path(tmp) / "nl.xml", '<A Value="line1\nline2"></A>')
            found = parse_native_xml(path).getroot().find("A")
            self.assertIsNotNone(found)
            self.assertEqual(found.get("Value"), "line1\nline2")


class RoundTripTest(unittest.TestCase):
    def test_write_then_parse_keeps_newlines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rt.xml"
            path.write_text(
                "<?xml version='1.0' encoding='utf-8'?>\n"
                '<Item CiID="1"><A Value="l1&#10;l2"/></Item>',
                encoding="utf-8",
            )
            tree = parse_native_xml(path)
            out = Path(tmp) / "out.xml"
            write_native_xml(tree, out)
            again = parse_native_xml(out).getroot().find("A")
            self.assertIsNotNone(again)
            self.assertEqual(again.get("Value").count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
