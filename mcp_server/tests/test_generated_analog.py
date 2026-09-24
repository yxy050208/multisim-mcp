"""Regressions exposed by opening and simulating generated native projects."""
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from multisim_mcp.native_analog_acceptance import cutoff_frequency, rc_step_response
from multisim_mcp.schematic_builder import (
    ComponentSpec, _configure_component_semantics, _set_component_value,
    _symbol_pin_info, build_schematic, template_search_paths,
)


class GeneratedAnalogTest(unittest.TestCase):
    def test_vendor_opamp_cannot_silently_become_ideal(self):
        with self.assertRaisesRegex(ValueError, 'not verified'):
            _configure_component_semantics(ET.Element('Item'),
                ComponentSpec(kind='OPAMP5',refdes='XU1',nodes=['a','b','vcc','vss','out'],model='LM741'))

    def carrier(self):
        root = ET.fromstring('<Item><CiComponent><Attributes><Item><CiaSpiceTmpltExprt String="broken"/>'
                             '</Item><Item><CiaParamList><doubles/><parameters/></CiaParamList></Item></Attributes></CiComponent></Item>')
        for tag in ('doubles', 'parameters'):
            for i in range(16):
                ET.SubElement(root.find(f'.//{tag}'), 'Item', Value='15.' if tag == 'doubles' else '&ASC15')
        return root

    def test_small_and_fractional_component_values_are_valid_numbers(self):
        for value in (100e-9, .1, 1000, 1e12):
            root = self.carrier()
            _set_component_value(root, 'C', str(value), value)
            self.assertEqual(float(root.find('.//doubles')[1].get('Value')), value)

    def test_resistor_is_ideal_and_parameter_editable(self):
        root = self.carrier()
        _configure_component_semantics(root, ComponentSpec('R', 'R1', ['in', 'out'], '1k'))
        self.assertEqual(root.find('.//CiaSpiceTmpltExprt').get('String'), '&ASCr%p %t1 %t2 #1')

    def test_source_polarity_and_display_properties_follow_request(self):
        root = self.carrier()
        _configure_component_semantics(root, ComponentSpec('V', 'V1', ['in', '0'],
            model='DC 1 AC 2 PULSE(0 1 100u 1u 1u 2m 4m)'))
        expression = root.find('.//CiaSpiceTmpltExprt').get('String')
        self.assertTrue(expression.startswith('&ASCv%p %t2 %t1 DC #1 AC #3'))
        self.assertEqual(float(root.find('.//doubles')[1].get('Value')), 1)
        self.assertEqual(float(root.find('.//doubles')[3].get('Value')), 2)
        self.assertIn('PULSE(0 1 100u 1u 1u 2m 4m)', expression)

    def test_pin_coordinates_include_nested_affine_transforms(self):
        root = ET.fromstring('''<Item><CIITSymbolComp Transformer-M00="0" Transformer-M01="1"
            Transformer-M10="-1" Transformer-M11="0" Transformer-M20="999" Transformer-M21="999">
            <Objects><Item Class="CIITPinSymbolComp"><CIITPinSymbolComp PortID="p"
            Transformer-M00="-1" Transformer-M11="-1" Transformer-M20="396" Transformer-M21="171">
            <Objects><Item ID="c" Class="CIITPinConnectorComp"><CIITPinConnectorComp ptCenterX="72" ptCenterY="63"/>
            </Item></Objects></CIITPinSymbolComp></Item></Objects></CIITSymbolComp></Item>''')
        point = _symbol_pin_info(root)['p']
        self.assertEqual((point['local_x'], point['local_y']), (-108, 324))

    def test_cutoff_rejects_incomplete_or_reversed_sweeps(self):
        for frequency, gain in (([1, 2], [1]), ([2, 1], [1, .1]), ([1, 2], [1, 1]), ([1, 2], [1, math.nan])):
            with self.assertRaises(ValueError):
                cutoff_frequency(frequency, gain)

    def test_cutoff_uses_half_power_not_half_voltage(self):
        self.assertAlmostEqual(cutoff_frequency([100, 1000, 10000], [1, math.sqrt(.5), .1]), 1000)

    def test_transient_reference_accounts_for_delay_and_finite_rise(self):
        values = rc_step_response([0, 100e-6, 101e-6, 201e-6, 1e-3], 1000)
        self.assertEqual(values[:2], [0, 0])
        self.assertAlmostEqual(values[2], 1 - 100 * (1 - math.exp(-.01)))
        self.assertGreater(values[3], .63)
        self.assertLess(values[3], .64)
        self.assertGreater(values[-1], .999)

    def test_generated_template_probes_are_unique_and_layout_passes(self):
        if not any((p / 'minimal.ms14.xml').is_file() for p in template_search_paths()):
            self.skipTest('requires a licensed local template pack')
        for shunt in ('R2 out 0 1k', 'C1 out 0 100n'):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'circuit.xml'
                result = build_schematic('V1 in 0 DC 10\nR1 in out 1k\n' + shunt + '\n.end', path, probe_nets=['in', 'out'])
                root = ET.parse(path)
            self.assertEqual(result['layout_validation']['status'], 'pass', result['layout_validation'])
            self.assertEqual([p['voltage_output'] for p in result['probes']], ['V(OutProbe)', 'V(OutProbe1)'])
            for attribute in ('UniqueID', 'FileDataPackageID'):
                self.assertEqual(len({p.get(attribute) for p in root.findall('.//CIITProbeExtComponent')}), 2)
            package_ids = {p.get('FileDataPackageID') for p in root.findall('.//CIITProbeExtComponent')}
            self.assertTrue(package_ids.issubset({p.get('CompLongName') for p in root.findall('.//CSourceSymbolCollectNode')}))


if __name__ == '__main__':
    unittest.main()
