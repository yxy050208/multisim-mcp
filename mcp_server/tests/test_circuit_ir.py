import unittest
from multisim_mcp.circuit_ir import CircuitIR, IrComponent, IrConnection

class CircuitIrTest(unittest.TestCase):
    def test_valid_divider(self):
        ir = CircuitIR([IrComponent("R1","R"), IrComponent("R2","R"), IrComponent("V1","V")], [
            IrConnection("in","V1.2"), IrConnection("in","R1.1"),
            IrConnection("out","R1.2"), IrConnection("out","R2.1"),
            IrConnection("0","R2.2"), IrConnection("0","V1.1")])
        self.assertEqual(ir.validate(), [])
    def test_unknown_endpoint(self):
        ir = CircuitIR([IrComponent("R1","R")], [IrConnection("n","X.1")])
        self.assertTrue(ir.validate())
    def test_to_spice(self):
        ir = CircuitIR([IrComponent("R1","R","10k"), IrComponent("R2","R","10k"), IrComponent("V1","V","10")], [
            IrConnection("in","V1.2"), IrConnection("in","R1.1"), IrConnection("out","R1.2"), IrConnection("out","R2.1"), IrConnection("0","R2.2"), IrConnection("0","V1.1")])
        spice = ir.to_spice()
        self.assertIn("R1 in out 10k", spice)
        self.assertIn("V1 0 in DC 10", spice)
