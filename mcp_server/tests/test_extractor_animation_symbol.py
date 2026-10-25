"""Regression tests for carrier extraction from GUI-saved designs.

Multisim's shipped samples embed each placed component's symbol as
CIITSymbolComp. Designs saved by the Multisim GUI itself store the symbol on
the animation layer as CIITAnimationSymbolComp with no CIITSymbolComp anywhere
in the file, which used to make every normally saved user design unusable as a
carrier source.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.extract_native_component_templates import extract_templates


def _fixture(symbol_class: str) -> str:
    """A minimal one-component design whose symbol uses the given class."""
    # Sample-style symbols carry the placement transform on their root; the
    # animation layer does not (the extraction adds the canonical identity).
    transform = (
        ' Transformer-M00="1." Transformer-M01="0." Transformer-M10="0."'
        ' Transformer-M11="1." Transformer-M20="0." Transformer-M21="0."'
        if symbol_class == "CIITSymbolComp"
        else ""
    )
    return f"""<?xml version='1.0' encoding='utf-8'?>
<MSMElectronicsWorkbench>
<Item CiID="100" Class="CiComponent">
 <CiComponent Class="CiComponent" LocalName="&amp;ASCU1" Circuit="1">
  <Attributes><Item /></Attributes>
  <Ports><Item /></Ports>
 </CiComponent>
</Item>
<Item ID="194459304" Class="{symbol_class}">
 <{symbol_class} Class="{symbol_class}" instID="0" CiComponent="100"{transform}>
  <Objects>
   <Item ID="1" Class="CIITPinSymbolComp">
    <CIITPinSymbolComp Class="CIITPinSymbolComp" PortID="201">
     <Objects>
      <Item ID="11" Class="CIITPinConnectorComp">
       <CIITPinConnectorComp Class="CIITPinConnectorComp" ptCenterX="10" ptCenterY="20"/>
      </Item>
     </Objects>
    </CIITPinSymbolComp>
   </Item>
   <Item ID="2" Class="CIITPinSymbolComp">
    <CIITPinSymbolComp Class="CIITPinSymbolComp" PortID="202">
     <Objects>
      <Item ID="12" Class="CIITPinConnectorComp">
       <CIITPinConnectorComp Class="CIITPinConnectorComp" ptCenterX="60" ptCenterY="20"/>
      </Item>
     </Objects>
    </CIITPinSymbolComp>
   </Item>
  </Objects>
 </{symbol_class}>
</Item>
<Item CiID="201" Class="CiPort">
 <CiPort Class="CiPort" LocalName="&amp;ASCGND" Component="100">
  <Attributes><Item /></Attributes>
 </CiPort>
</Item>
<Item CiID="202" Class="CiPort">
 <CiPort Class="CiPort" LocalName="&amp;ASCVCC" Component="100">
  <Attributes><Item /></Attributes>
 </CiPort>
</Item>
</MSMElectronicsWorkbench>
"""


class AnimationSymbolCarrierTest(unittest.TestCase):
    def _extract(self, symbol_class: str) -> list[Path]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "design.xml"
            source.write_text(_fixture(symbol_class), encoding="utf-8")
            out = root / "templates"
            written = extract_templates(source, "U1", "TIMER8", out)
            self.result = {path.name: path.read_text(encoding="utf-8") for path in written}
            return written

    def test_gui_saved_animation_symbol_is_accepted(self) -> None:
        written = self._extract("CIITAnimationSymbolComp")
        names = {path.name for path in written}
        self.assertIn("timer8_element.xml", names)
        self.assertIn("sym_timer8.xml", names)
        self.assertIn("timer8_port1.xml", names)
        # The animation root must be normalized to the class the builder reads.
        sym = self.result["sym_timer8.xml"]
        self.assertIn("CIITSymbolComp", sym)
        self.assertNotIn("CIITAnimationSymbolComp", sym)
        # And it must carry the canonical transform the orientation gate checks.
        self.assertIn('Transformer-M00="1."', sym)
        self.assertIn('Transformer-M20="0."', sym)

    def test_sample_style_symbol_still_works(self) -> None:
        written = self._extract("CIITSymbolComp")
        names = {path.name for path in written}
        self.assertIn("sym_timer8.xml", names)
        # No normalization was needed: the file must not contain the animation class.
        self.assertNotIn("CIITAnimationSymbolComp", self.result["sym_timer8.xml"])

    def test_design_without_any_symbol_fails_clearly(self) -> None:
        stripped = _fixture("CIITAnimationSymbolComp").replace(
            'CiComponent="100"', 'CiComponent="999999"'
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "design.xml"
            source.write_text(stripped, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "No CIITSymbolComp was linked"):
                extract_templates(source, "U1", "TIMER8", root / "out")


if __name__ == "__main__":
    unittest.main()
