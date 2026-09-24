"""Real Multisim regression for generated native component families.

Run manually with a compatible 32-bit Python and configured EWD/EWE codec.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from multisim_mcp.server import client, create_schematic_from_netlist


CASES = {
    "rlc_sources": """\
V1 in 0 DC 5
I1 in n1 DC 1m
R1 n1 n2 1k
L1 n2 out 10m
C1 out 0 1u
L2 aux 0 2.5m
K1 L1 L2 0.98
.end
""",
    "semiconductors": """\
V1 vdd 0 DC 5
D1 vdd d 1N4001
Q1 c1 b1 0 2N3904
Q2 c2 b2 vdd 2N3906
M1 d1 g1 0 0 NMOS
M2 d2 g2 vdd vdd PMOS
S1 sw 0 g1 0 SWMOD
J1 jd jg 0 JMOD
Z1 zd zg 0 ZMOD
W1 wo 0 V1 WMOD ON
.model NMOS NMOS(Level=1)
.model PMOS PMOS(Level=1)
.model SWMOD SW(Ron=1 Roff=1G Vt=2)
.model JMOD NJF(Beta=1m)
.model ZMOD NMF(Beta=2m)
.model WMOD CSW(Ron=1 Roff=1G It=1m Ih=0.1m)
.end
""",
    "opamp": """\
V1 vp 0 DC 15
V2 vn 0 DC -15
XU1 in 0 vp vn out OPAMP5
R1 out 0 10k
.end
""",
    "controlled_sources": """\
VCTRL sense 0 DC 1
E1 eo 0 sense 0 10
F1 fo 0 VCTRL 2
G1 go 0 sense 0 3m
H1 ho 0 VCTRL 4k
B1 bo 0 V={V(sense)*5}
B2 bio 0 I={V(sense)/1000}
R1 eo 0 1k
R2 fo 0 1k
R3 go 0 1k
R4 ho 0 1k
.end
""",
    "waveforms_and_line": """\
V1 in 0 SIN(0 1 1k)
I1 load 0 PULSE(0 1m 1u 1n 1n 5u 10u)
T1 in 0 out 0 Z0=50 TD=10n
O1 in 0 lossy 0 OMOD
U1 in urc 0 UMOD L=1 N=8
R1 out 0 50
.model OMOD LTRA(R=1 L=1u G=0 C=1p LEN=1)
.model UMOD URC(RPERL=1k CPERL=1u)
.end
""",
    "generic_subcircuits": """\
X2 a b TWO_PIN
X3 a b c THREE_PIN
X4 a b c d FOUR_PIN
X5 a b c d e FIVE_PIN
X8 a b c d e f g h EIGHT_PIN
.subckt TWO_PIN p n
R99 p n 1k
.ends TWO_PIN
.subckt THREE_PIN a b c
R98 a b 1k
R97 b c 1k
.ends THREE_PIN
.subckt FOUR_PIN a b c d
R96 a b 1k
R95 c d 1k
.ends FOUR_PIN
.subckt FIVE_PIN a b c d e
R94 a b 1k
R93 c d 1k
R92 d e 1k
.ends FIVE_PIN
.subckt EIGHT_PIN a b c d e f g h
R91 a b 1k
R90 c d 1k
R89 e f 1k
R88 g h 1k
.ends EIGHT_PIN
.end
""",
    "digital_preview": """\
A1 din n1 vdd 0 NOT
A2 n1 enable n2 vdd 0 AND2
A3 n2 bypass dout vdd 0 OR2
A4 n1 n2 din vdd 0 dout bypass JKFF
A5 din enable nandout vdd 0 NAND2
A6 din enable norout vdd 0 NOR2
A7 din enable xorout vdd 0 XOR2
A8 din enable xnorout vdd 0 XNOR2
.end
""",
    "virtual_instruments": """\
XFG1 out 0 inv FGEN WAVE=SINE FREQ=1k AMPLITUDE=2 OFFSET=0.5
R1 out 0 1k
R2 inv 0 1k
XSC1 out inv 0 0 out 0 OSCILLOSCOPE
.end
""",
}


def main() -> None:
    results: dict[str, dict] = {}
    selected = CASES
    if len(sys.argv) > 1:
        requested = sys.argv[1]
        if requested not in CASES:
            raise SystemExit(f"Unknown case {requested!r}; choose from {', '.join(CASES)}")
        selected = {requested: CASES[requested]}
    with tempfile.TemporaryDirectory(prefix="multisim-mcp-components-") as tmp:
        root = Path(tmp)
        for name, netlist in selected.items():
            print(f"Running {name}...", file=sys.stderr, flush=True)
            case_dir = root / name
            try:
                result = create_schematic_from_netlist(
                    netlist,
                    str(case_dir / "circuit.ms14"),
                    open_after_build=True,
                    image_path=str(case_dir / "schematic.png"),
                    overwrite=False,
                )
                verification = result["verification"]
                assert verification["native_netlist_complete"], verification
                results[name] = {
                    "components": verification["components"],
                    "virtual_instruments": verification["virtual_instruments"],
                    "native": verification["native_netlist_components"],
                    "model_warnings": result["build"]["model_warnings"],
                }
            finally:
                client.disconnect()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
