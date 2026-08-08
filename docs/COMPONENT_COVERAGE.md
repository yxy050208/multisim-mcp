# Component coverage

"All Multisim components" is not a finite SPICE checklist: the master database
contains many vendor part numbers, multi-section devices, electromechanical and
power models, interactive controls, digital models, MCU peripherals, and virtual
instruments. The project therefore defines coverage by capability tier.

## Current native topology coverage

| Family | Input form | Native topology | Value/model fidelity |
| --- | --- | --- | --- |
| Resistor | `R...` | Verified | Value mapped |
| Capacitor | `C...` | Verified | Value mapped |
| Inductor | `L...` | Verified | Value mapped |
| DC voltage source | `V...` | Verified | DC value mapped |
| DC current source | `I...` | Verified | DC value mapped |
| Waveform sources | `V... SIN/PULSE/...`, `I...` | Verified | Source specification retained |
| VCVS/VCCS | `E...` / `G...` | Verified experimental carrier | Linear gain mapped |
| CCCS/CCVS | `F...` / `H...` | Verified experimental carrier | Control source and linear gain mapped |
| Diode | `D...` | Verified | Native 1N4001 family |
| NPN/PNP BJT | `Q...` | Verified | Native 2N3904/2N3906 families |
| Four-terminal NMOS/PMOS | `M...` | Verified | Explicit `.model` and instance parameters embedded; native-alias parameters pending |
| Behavioral source | `B... V=...` / `B... I=...` | Verified experimental carrier | Expression retained |
| Lossless transmission line | `T...` | Verified experimental carrier | Line parameters retained |
| Lossy / uniform RC lines | `O...` / `U...` | Verified experimental carrier | Per-instance LTRA/URC `.model` embedded |
| Coupled inductors | `K... L1 L2 k` | Verified experimental carrier | Coupling and referenced inductors retained |
| Voltage switch | `S...` | Verified experimental carrier | Per-instance `.model` embedded |
| Current-controlled switch | `W...` | Verified experimental carrier | Control source and per-instance `.model` embedded |
| N/P JFET and MESFET | `J...` / `Z...` | Verified experimental carrier | Per-instance `.model` embedded |
| Five-terminal op-amp | `X... OPAMP5` | Verified | Native virtual op-amp |
| Generic subcircuit | two-to-sixteen-terminal `X...` | Verified experimental carrier | Invocation retained; body remains in experiment netlist |
| NOT / AND / OR / NAND / NOR / XOR / XNOR | `A...` digital aliases | Native preview verified | Open/export and real truth-table transient regression verified |
| JK flip-flop | `A... J K CLK SET RESET Q QBAR JKFF` | Native preview verified | Open/export and real toggle-timing regression verified |
| Four-channel oscilloscope | `XSC... A B C D EXT+ EXT- OSCILLOSCOPE` | Native instrument verified | Front-panel state survives native save; experiment CSV/SVG remains authoritative |
| Function generator | `XFG... + COM - FGEN NAME=VALUE...` | Native instrument verified | Sine/square/triangle settings and command-engine equivalent supported |
| Ground/named nets | `0`, node names | Verified | Complete |

Every opened generated design is exported back through Multisim's native netlist
report. Missing components make generation fail instead of being reported as a
successful schematic.

## Coverage tiers

1. **Native primitive:** editable Multisim symbol, pins, value/model mapping,
   reverse-netlist verification, and simulation regression.
2. **Native family:** one template plus an explicit model alias/parameter mapper
   covers a vendor/model family.
3. **Generic SPICE/subcircuit:** arbitrary safe netlists simulate correctly and
   receive a generated block symbol when no native database mapping exists.
4. **Interactive/digital/instrument:** event behavior and instrument state require
   dedicated adapters in addition to schematic pins.

The practical release target is complete SPICE primitive coverage plus a generic
subcircuit fallback, followed by high-value Multisim database families. Mirroring
every database SKU in the repository would be brittle and may violate NI/vendor
redistribution terms; templates must remain provenance-tracked.

The four controlled-source types pass native open/export regression. Their
electrical behavior is standard E/F/G/H SPICE, but the generated editor symbols
are temporary carrier shapes rather than the final controlled-source diamonds.

## Next families

- Dedicated artwork for controlled, behavioral, switch, line, and generic symbols.
- Dedicated transformer artwork, relays, potentiometers, and crystals.
- Zener/Schottky/LED/SCR/triac and three-terminal MOS variants.
- D/T flip-flops, counters, ADC/DAC, and mixed-signal bridges.
- Multimeter, Bode plotter, probes, and logic analyzer.
- Generic symbols above sixteen pins and embedded subcircuit bodies.
