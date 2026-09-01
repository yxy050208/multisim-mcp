"""Tests for deterministic E-series value generation."""

from __future__ import annotations

import unittest
from decimal import Decimal

from multisim_mcp.preferred_values import (
    format_spice_scalar,
    generate_preferred_values,
    parse_spice_scalar,
    spice_value_key,
)


class PreferredValuesTest(unittest.TestCase):
    def test_e12_range_is_inclusive_and_engineering_formatted(self) -> None:
        self.assertEqual(
            generate_preferred_values("e12", "1k", "10k"),
            [
                "1k", "1.2k", "1.5k", "1.8k", "2.2k", "2.7k", "3.3k",
                "3.9k", "4.7k", "5.6k", "6.8k", "8.2k", "10k",
            ],
        )

    def test_e96_narrow_range_uses_standard_table(self) -> None:
        self.assertEqual(
            generate_preferred_values("E96", "1k", "1.1k"),
            ["1k", "1.02k", "1.05k", "1.07k", "1.1k"],
        )

    def test_spice_suffixes_preserve_milli_mega_and_micro_semantics(self) -> None:
        self.assertEqual(parse_spice_scalar("1m"), Decimal("0.001"))
        self.assertEqual(parse_spice_scalar("1Meg"), Decimal("1e6"))
        self.assertEqual(parse_spice_scalar("2.2µ"), Decimal("2.2e-6"))
        self.assertEqual(spice_value_key("1k"), spice_value_key("1000"))

    def test_formatter_covers_supported_engineering_range(self) -> None:
        self.assertEqual(format_spice_scalar(Decimal("4.7e-9")), "4.7n")
        self.assertEqual(format_spice_scalar(Decimal("2.2e6")), "2.2Meg")

    def test_invalid_series_bounds_and_suffixes_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "one of"):
            generate_preferred_values("E192", "1", "10")
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            generate_preferred_values("E12", "10k", "1k")
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            generate_preferred_values("E12", "0", "1k")
        with self.assertRaisesRegex(ValueError, "suffix"):
            parse_spice_scalar("1kohm")


if __name__ == "__main__":
    unittest.main()
