"""Regression tests for native-carrier rendering in the command netlist.

The Multisim command engine cannot resolve native carrier names (TIMER8, DFF8,
CD4017): an unrendered X-line makes it drop the instance, so a generated design
simulates as if the part were absent. prepare_simulation_netlist renders each
such instance from the carrier's own extracted templates and embeds the
referenced model body once per model name.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from multisim_mcp.schematic_builder import prepare_simulation_netlist

TIMER_ELEMENT = (
    "<Item CiID='1' Class='CiComponent'><CiComponent Class='CiComponent'>"
    "<Attributes><Item><CiaSpiceTmpltExprt Class='CiaSpiceTmpltExprt'"
    " String='&amp;ASCx%p %tGND %tTRI %tOUT %tRST %tCON %tTHR %tDIS %tVCC %m'/>"
    "</Item></Attributes></CiComponent></Item>"
)
TIMER_MODEL = (
    "<Item CiID='2' Class='CiModel'><CiModel Class='CiModel'>"
    "<Attributes><Item><CiaCString Class='CiaCString'"
    " String='&amp;ASC.subckt LM555__TIMER__1 0 2 3 4 5 6 7 8"
    " RIN 8 5 5k\\n.ends'/>"
    "</Item></Attributes></CiModel></Item>"
)
DFF_ELEMENT = (
    "<Item CiID='3' Class='CiComponent'><CiComponent Class='CiComponent'>"
    "<Attributes><Item><CiaSpiceTmpltExprt Class='CiaSpiceTmpltExprt'"
    " String='&amp;ASCa%p_a [%t1D?%t:d%t;1D %t~1PR?%t:d%t;~1PR"
    " %t~1CLR?%t:d%t;~1CLR %t1CLK?%t:d%t;1CLK %t1Q?%t:d%t;1Q"
    " %t~1Q?%t:d%t;~1Q] %m'/>"
    "</Item></Attributes></CiComponent></Item>"
)
DFF_MODEL = (
    "<Item CiID='4' Class='CiModel'><CiModel Class='CiModel'>"
    "<Attributes><Item><CiaCString Class='CiaCString'"
    " String='&amp;ASC.MODEL 7474__74STD__2 d_chip (behaviour= \"flag\")'/>"
    "</Item></Attributes></CiModel></Item>"
)


def _write_pack(root: Path) -> None:
    (root / "timer8_element.xml").write_text(TIMER_ELEMENT, encoding="utf-8")
    (root / "timer8_model.xml").write_text(TIMER_MODEL, encoding="utf-8")
    (root / "dff8_element.xml").write_text(DFF_ELEMENT, encoding="utf-8")
    (root / "dff8_model.xml").write_text(DFF_MODEL, encoding="utf-8")


class CarrierRenderingTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        _write_pack(Path(self._tmp.name))
        patcher = patch(
            "multisim_mcp.schematic_builder.template_search_paths",
            return_value=[Path(self._tmp.name)],
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_timer8_instance_is_rendered_and_subckt_embedded(self) -> None:
        out = prepare_simulation_netlist(
            "VCC vcc 0 DC 5\n"
            "XU1 0 tri out vcc ncon thr dis vcc TIMER8\n"
            ".end\n"
        )
        self.assertIn("xXU1 0 tri out vcc ncon thr dis vcc LM555__TIMER__1", out)
        self.assertIn(".subckt LM555__TIMER__1 0 2 3 4 5 6 7 8", out)
        self.assertIn("RIN 8 5 5k", out)
        self.assertIn(".ends", out)
        # The unrendered alias must not survive anywhere.
        self.assertNotIn(" TIMER8\n", out)

    def test_two_instances_share_one_embedded_subckt(self) -> None:
        out = prepare_simulation_netlist(
            "XU1 a b c d e f g h TIMER8\n"
            "XU2 h g f e d c b a TIMER8\n"
            ".end\n"
        )
        self.assertEqual(out.count(".subckt LM555__TIMER__1"), 1)
        self.assertIn("xXU1 a b c d e f g h LM555__TIMER__1", out)
        self.assertIn("xXU2 h g f e d c b a LM555__TIMER__1", out)

    def test_conditional_tokens_resolve_to_connected_nets(self) -> None:
        out = prepare_simulation_netlist(
            "XU1 d pr clr clk q nq 7474N\n.end\n"
        )
        # Every conditional token resolves to the connected net; the internal
        # dangling fallback (d<name>) must never appear for a full instance.
        self.assertNotIn("%t", out)
        self.assertIn("aXU1_a [d pr clr clk q nq] 7474__74STD__2", out)
        self.assertIn(".MODEL 7474__74STD__2 d_chip", out)

    def test_missing_carrier_templates_leave_the_line_unchanged(self) -> None:
        out = prepare_simulation_netlist(
            "XU1 a b c d e f g h CD4017\n.end\n"
        )
        # No cd4017 templates in the fixture pack: the engine's own error must
        # name the part rather than the tool emitting a different circuit.
        self.assertIn("XU1 a b c d e f g h CD4017", out)

    def test_terminal_count_mismatch_leaves_the_line_for_the_engine(self) -> None:
        # The alias set is shared with user-defined subcircuit names: an
        # X-line whose arity disagrees with the carrier may be the user's own
        # subcircuit, so the renderer stands down instead of rejecting it.
        out = prepare_simulation_netlist("XU1 a b c d TIMER8\n.end\n")
        self.assertIn("XU1 a b c d TIMER8", out)


if __name__ == "__main__":
    unittest.main()
