"""Local-carrier regressions for defects visible only in native artifacts."""
import tempfile
import unittest
from pathlib import Path

from multisim_mcp.generated_analog_run import vendor_model_fingerprints
from multisim_mcp.native_xml import parse_native_xml, write_native_xml
from multisim_mcp.natural_common_emitter import parse_natural_common_emitter
from multisim_mcp.natural_common_emitter_run import verify_ce_presentation
from multisim_mcp.schematic_builder import build_schematic, parse_netlist, voltage_source_stem, _load_template, _configure_component_semantics, ComponentSpec


class CommonEmitterNativeContractTest(unittest.TestCase):
    def setUp(self):
        self.plan = parse_natural_common_emitter('12V增益10倍NPN共射放大器')
        self.parts = parse_netlist(self.plan['proposal']['netlist']).components
        if {voltage_source_stem(p) for p in self.parts if p.kind == 'V'} != {'vdc','vpulse'}:
            self.skipTest('requires newly bootstrapped local native source templates')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'source.xml'
        self.build = build_schematic(self.plan['proposal']['netlist'],self.path,probe_nets=self.plan['proposal']['probe_nets'])

    def test_actual_source_identity_parameters_and_geometry(self):
        self.assertTrue(verify_ce_presentation(self.path,self.plan)['ok'])
        self.assertEqual(self.build['layout_profile'],'common_emitter')
        self.assertEqual(self.build['layout_validation']['status'],'pass')

    def test_cached_label_cannot_hide_wrong_voltage_parameter(self):
        tree=parse_native_xml(self.path)
        component=next(c for c in tree.getroot().iter('CiComponent') if c.get('LocalName')=='&ASCVCC')
        component.findall('.//CiaParamList/doubles/Item')[1].set('Value','99')
        write_native_xml(tree,self.path)
        self.assertFalse(verify_ce_presentation(self.path,self.plan)['ok'])

    def test_missing_linked_npn_model_is_rejected(self):
        self.assertIn('Q1',vendor_model_fingerprints(self.path,self.parts))
        tree=parse_native_xml(self.path)
        q=next(c for c in tree.getroot().iter('CiComponent') if c.get('LocalName')=='&ASCQ1')
        q.set('Model','missing')
        write_native_xml(tree,self.path)
        with self.assertRaisesRegex(ValueError,'linked model'):
            vendor_model_fingerprints(self.path,self.parts)

    def test_native_pulse_properties_preserve_ac_phase(self):
        import copy
        carrier = copy.deepcopy(_load_template('vpulse_element.xml'))
        spec = ComponentSpec('V','VIN',['in','0'],model='DC 0 AC 2 -90 PULSE(0 1m 1m 1u 1u 1m 2m)')
        _configure_component_semantics(carrier,spec)
        values=carrier.findall('.//CiaParamList/doubles/Item')
        self.assertEqual(float(values[15].get('Value')),2)
        self.assertEqual(float(values[17].get('Value')),-90)
