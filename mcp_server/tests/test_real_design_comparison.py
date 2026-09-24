"""Opt-in real Multisim gate for complete-design variant comparison."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.design_comparison import (
    DesignVariantComparisonService,
    read_design_comparison,
)
from multisim_mcp.eda_core import CircuitComponent, CircuitDesign


@unittest.skipUnless(
    os.environ.get("MULTISIM_MCP_RUN_REAL_TESTS") == "1",
    "set MULTISIM_MCP_RUN_REAL_TESTS=1 on a licensed Multisim workstation",
)
class RealDesignComparisonTest(unittest.TestCase):
    def test_voltage_divider_ranks_three_complete_variants(self) -> None:
        from multisim_mcp.server import _experiment_application_service

        def design(variant_id: str, r2: str) -> CircuitDesign:
            return CircuitDesign(
                design_id=f"real-comparison-{variant_id}",
                title=f"Real comparison {variant_id}",
                revision=0,
                components=(
                    CircuitComponent("V1", "V", ("in", "0"), value="10"),
                    CircuitComponent("R1", "R", ("in", "out"), value="1k"),
                    CircuitComponent("R2", "R", ("out", "0"), value=r2),
                ),
                source_netlist=(
                    f"V1 in 0 10\nR1 in out 1k\nR2 out 0 {r2}\n.end\n"
                ),
            )

        variants = {
            "low": design("low", "500"),
            "balanced": design("balanced", "1k"),
            "target": design("target", "2k"),
        }
        originals = {name: item.to_dict() for name, item in variants.items()}
        spec = {
            "schema_version": 1,
            "title": "Real divider design comparison",
            "commands": "op",
            "requirements": [
                {
                    "id": "divider-output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "between",
                    "lower": 3.0,
                    "upper": 8.0,
                    "unit": "V",
                }
            ],
            "theoretical_values": {"divider-output": 6.6666666667},
            "objective": {
                "requirement_id": "divider-output",
                "goal": "target",
                "target": 6.6666666667,
            },
        }
        with tempfile.TemporaryDirectory(prefix="multisim-mcp-real-compare-") as tmp:
            output = Path(tmp) / "comparison"
            result = DesignVariantComparisonService(
                _experiment_application_service()
            ).run(
                variants,
                spec,
                str(output),
                timeout_per_experiment=120.0,
                max_points=2000,
            )
            stored = read_design_comparison(str(output), verify=True)
            self.assertTrue(result["success"], result)
            self.assertEqual(result["status"], "ranked")
            self.assertEqual(result["selected_variant"]["variant_id"], "target")
            self.assertEqual(result["experiments_attempted"], 3)
            self.assertEqual(stored["ranked_feasible_variant_ids"][0], "target")
            self.assertEqual(
                {name: item.to_dict() for name, item in variants.items()}, originals
            )


if __name__ == "__main__":
    unittest.main()
