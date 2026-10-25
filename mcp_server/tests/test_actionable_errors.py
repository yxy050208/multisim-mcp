"""Actionable error surfaces: COM failures, encodings, outputs, identifiers."""

import locale
import unittest

from multisim_mcp.com_worker import describe_com_error
from multisim_mcp.multisim_client import (
    decode_multisim_text,
    describe_missing_output,
)
from multisim_mcp.safety import validate_ascii_netlist_text


class DescribeComErrorTest(unittest.TestCase):
    def test_excepinfo_text_is_extracted(self) -> None:
        class com_error(Exception):
            pass

        exc = com_error()
        exc.args = (
            -2147352567,
            "Exception occurred.",
            (0, "Multisim", "Cannot open file", None, 0, -2147352567),
            None,
        )
        self.assertEqual(
            describe_com_error(exc),
            "COM call failed (HRESULT 0x80020009): Multisim: Cannot open file",
        )

    def test_regular_exceptions_pass_through(self) -> None:
        self.assertIsNone(describe_com_error(RuntimeError("No circuit is open")))


class DecodeMultisimTextTest(unittest.TestCase):
    def test_utf8_payload_round_trips(self) -> None:
        self.assertEqual(decode_multisim_text("中文 net".encode("utf-8")), "中文 net")

    @unittest.skipUnless(
        locale.getpreferredencoding(False).lower() in {"cp936", "gbk"},
        "needs a GBK locale to exercise the ANSI fallback",
    )
    def test_ansi_payload_falls_back_to_the_code_page(self) -> None:
        self.assertEqual(decode_multisim_text("中文".encode("gbk")), "中文")

    def test_undecodable_payload_never_raises(self) -> None:
        self.assertIsInstance(decode_multisim_text(b"\xff\xfe\x00bad"), str)


class DescribeMissingOutputTest(unittest.TestCase):
    def test_lists_available_outputs(self) -> None:
        message = describe_missing_output("V(nope)", ["V(a)", "V(b)"])
        self.assertIn("V(nope)", message)
        self.assertIn("V(a), V(b)", message)
        self.assertIn("enum_outputs", message)

    def test_empty_circuit_gets_probe_guidance(self) -> None:
        self.assertIn("no simulation outputs", describe_missing_output("V(x)", []))


class ValidateAsciiNetlistTextTest(unittest.TestCase):
    def test_rejects_non_ascii_identifiers(self) -> None:
        with self.assertRaisesRegex(ValueError, "line 2"):
            validate_ascii_netlist_text("* title\nR1 输入 out 1k\n")

    def test_allows_non_ascii_comments(self) -> None:
        validate_ascii_netlist_text("* 中文注释 ; also comment\nV1 in 0 DC 5\n")

    def test_allows_plain_ascii(self) -> None:
        validate_ascii_netlist_text("R1 in out 1k\nR2 out 0 2k\n")


if __name__ == "__main__":
    unittest.main()
