"""Bounded mixed digital/analog multi-board regression contract.

The case is deliberately small: a DNOT4 source board drives an RC load board
through the verified HDR1X4 mapping.  Boundary stimuli are explicit fixtures;
the module never infers a source from a connector or a net name.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .eda_core import CircuitComponent, CircuitDesign
from .multiboard_plan import plan_multiboard_partition
from .hybrid_regression import ConverterTransferContract
from .native_analysis_series import ContinuousResponseContract, DigitalEdgeComparisonContract


def _fixture_digest(components: list[dict[str, Any]], ports: list[str]) -> str:
    payload = {"ports": ports, "components": components}
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class HybridMultiboardRegressionCase:
    case_id: str
    description: str
    target_version: str
    analysis: str
    analysis_options: dict[str, Any]

    def source_components(self) -> list[dict[str, Any]]:
        return [
            {"refdes": "VDD", "kind": "V", "nodes": ["high", "0"], "value": "5", "board": "logic"},
            {"refdes": "VIN", "kind": "V", "nodes": ["din", "0"],
             "model": "PULSE(0 5 0 1n 1n 20u 40u)", "board": "logic"},
            {"refdes": "A1", "kind": "DNOT4", "nodes": ["din", "sig", "high", "0"],
             "model": "NOT", "board": "logic"},
            {"refdes": "RBIAS", "kind": "R", "nodes": ["high", "0"], "value": "1G", "board": "analog"},
            {"refdes": "R1", "kind": "R", "nodes": ["sig", "filt"], "value": "1k", "board": "analog"},
            {"refdes": "C1", "kind": "C", "nodes": ["filt", "0"], "value": "1u", "board": "analog"},
            {"refdes": "RLOAD", "kind": "R", "nodes": ["filt", "0"], "value": "100k", "board": "analog"},
        ]

    def boards(self) -> list[dict[str, Any]]:
        return [
            {"id": "logic", "role": "digital-source", "max_components": 8, "max_connector_pins": 4},
            {"id": "analog", "role": "rc-load", "max_components": 8, "max_connector_pins": 4},
        ]

    def connector(self) -> dict[str, Any]:
        return {
            "id": "JLINK", "part": "HDR1X4", "boards": ["logic", "analog"],
            "instances": [{"board": "logic", "refdes": "J1"}, {"board": "analog", "refdes": "J1"}],
            "pins": [
                {"number": 1, "net": "sig", "signal_type": "digital", "direction": "bidirectional",
                 "board_directions": {"logic": "out", "analog": "in"}},
                {"number": 2, "net": "high", "signal_type": "power", "direction": "power",
                 "board_directions": {"logic": "out", "analog": "in"}},
                {"number": 3, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 4, "net": "0", "signal_type": "ground", "direction": "passive"},
            ],
        }

    def digital_fixture(self) -> dict[str, Any]:
        components = [
            {"refdes": "VFIXD", "kind": "V", "nodes": ["din_fix", "0"],
             "model": "PULSE(0 5 0 1n 1n 20u 40u)"},
            {"refdes": "AFIXD", "kind": "DNOT4", "nodes": ["din_fix", "sig", "high", "0"],
             "model": "NOT"},
        ]
        ports = ["sig", "high", "0"]
        return {
            "id": "analog-digital-fixture", "board_id": "analog", "kind": "circuit_fragment",
            "net": "sig", "ports": ports, "source_compiled_digest": _fixture_digest(components, ports),
            "model_scope": "explicit-digital-fixture", "components": components,
        }

    def fixtures(self) -> list[dict[str, Any]]:
        return [
            {"id": "logic-sig", "board_id": "logic", "kind": "observation", "net": "sig"},
            {"id": "logic-high", "board_id": "logic", "kind": "observation", "net": "high"},
            {"id": "analog-sig", "board_id": "analog", "kind": "observation", "net": "sig"},
            {"id": "analog-high", "board_id": "analog", "kind": "observation", "net": "high"},
            {"id": "analog-filt", "board_id": "analog", "kind": "observation", "net": "filt"},
            {"id": "logic-ground", "board_id": "logic", "kind": "ground_reference", "net": "0"},
            {"id": "analog-ground", "board_id": "analog", "kind": "ground_reference", "net": "0"},
            self.digital_fixture(),
            {"id": "analog-high-source", "board_id": "analog", "kind": "voltage_source",
             "net": "high", "reference_net": "0", "refdes": "VHIGH", "value": "5"},
        ]

    def continuous_response_contracts(self) -> dict[str, ContinuousResponseContract]:
        """Compare the receiving board's RC output with the complete reference."""
        return {
            "filt": ContinuousResponseContract(
                final_value_tolerance=0.02,
                extrema_value_tolerance=0.02,
                response_range_tolerance=0.02,
                time_tolerance=1e-5,
                minimum_samples=8,
            )
        }

    def build(self) -> tuple[CircuitDesign, dict[str, Any], list[dict[str, Any]]]:
        raw_components = self.source_components()
        design = CircuitDesign(
            design_id="hybrid-split-v1", title="Digital source to RC load",
            components=tuple(
                CircuitComponent.from_dict({key: value for key, value in item.items() if key != "board"})
                for item in raw_components
            ),
            nets=tuple(sorted({node for item in raw_components for node in item["nodes"]})),
        )
        partition = plan_multiboard_partition(raw_components, self.boards(), [self.connector()])
        if partition.get("feasible") is not True:
            raise ValueError(f"{self.case_id}: partition is infeasible: {partition.get('violations', [])}")
        return design, partition, copy.deepcopy(self.fixtures())

    def manifest(self) -> dict[str, Any]:
        design, partition, fixtures = self.build()
        return {
            "schema_version": 1, "case_id": self.case_id, "description": self.description,
            "target_version": self.target_version, "analysis": self.analysis,
            "analysis_options": copy.deepcopy(self.analysis_options),
            "design_id": design.design_id, "partition_digest": hashlib.sha256(
                json.dumps(partition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "fixture_digest": next(item["source_compiled_digest"] for item in fixtures if item["kind"] == "circuit_fragment"),
            "connector": self.connector(), "fixtures": fixtures,
        }


@dataclass(frozen=True)
class ControlledSourceMultiboardRegressionCase:
    """Two-board VCVS/VCCS source with an explicit receiving-board fixture."""

    case_id: str
    source_kind: str
    description: str
    source_value: str | None = None
    series_resistance: str = "1k"
    filter_capacitance: str = "1u"
    load_resistance: str = "100k"
    target_version: str = "14.3"
    analysis: str = "tran"
    analysis_options: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.source_kind not in {"E", "G"}:
            raise ValueError("controlled source case must use E (VCVS) or G (VCCS)")
        if self.source_value is None:
            object.__setattr__(self, "source_value", "1" if self.source_kind == "E" else "1m")
        if not isinstance(self.source_value, str) or not self.source_value.strip():
            raise ValueError("controlled source source_value must be a non-empty SPICE token")
        for name in ("series_resistance", "filter_capacitance", "load_resistance"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"controlled source {name} must be a non-empty SPICE token")
        if self.analysis_options is None:
            object.__setattr__(self, "analysis_options", {
                "sample_rate": 1_000_000.0,
                "num_samples": 200,
                "duration": 0.00016,
                "max_points": 2000,
                "series_alignment": "linear",
            })

    def source_components(self) -> list[dict[str, Any]]:
        controlled_board = "source" if self.source_kind == "E" else "load"
        controlled = {
            "refdes": "E1" if self.source_kind == "E" else "G1",
            "kind": self.source_kind,
            "nodes": ["raw", "0", "ctrl", "0"] if self.source_kind == "E"
            else ["0", "raw", "ctrl", "0"],
            "value": self.source_value,
            "board": controlled_board,
        }
        return [
            {"refdes": "VCTRL", "kind": "V", "nodes": ["ctrl", "0"],
             "model": "PULSE(0 5 0 1n 1n 40u 80u)", "board": "source"},
            controlled,
            {"refdes": "R1", "kind": "R", "nodes": ["raw", "filt"],
             "value": self.series_resistance, "board": "load"},
            {"refdes": "C1", "kind": "C", "nodes": ["filt", "0"],
             "value": self.filter_capacitance, "board": "load"},
            {"refdes": "RLOAD", "kind": "R", "nodes": ["filt", "0"],
             "value": self.load_resistance, "board": "load"},
        ]

    def boards(self) -> list[dict[str, Any]]:
        return [
            {"id": "source", "role": f"{self.source_kind.lower()}-source",
             "max_components": 4, "max_connector_pins": 4},
            {"id": "load", "role": "rc-load", "max_components": 6,
             "max_connector_pins": 4},
        ]

    def connector(self) -> dict[str, Any]:
        boundary_net = "raw" if self.source_kind == "E" else "ctrl"
        return {
            "id": "JLINK", "part": "HDR1X4", "boards": ["source", "load"],
            "instances": [
                {"board": "source", "refdes": "J1"},
                {"board": "load", "refdes": "J1"},
            ],
            "pins": [
                {"number": 1, "net": boundary_net, "signal_type": "analog",
                 "direction": "out", "board_directions": {"source": "out", "load": "in"}},
                {"number": 2, "net": "0", "signal_type": "ground",
                 "direction": "passive"},
                {"number": 3, "net": "0", "signal_type": "ground",
                 "direction": "passive"},
                {"number": 4, "net": "0", "signal_type": "ground",
                 "direction": "passive"},
            ],
        }

    def fixtures(self) -> list[dict[str, Any]]:
        if self.source_kind == "E":
            raw_high = 5.0 * float(self.source_value)
            return [
                {"id": "source-raw", "board_id": "source", "kind": "observation", "net": "raw"},
                {"id": "source-ctrl", "board_id": "source", "kind": "observation", "net": "ctrl"},
                {"id": "load-raw", "board_id": "load", "kind": "observation", "net": "raw"},
                {"id": "load-filt", "board_id": "load", "kind": "observation", "net": "filt"},
                {"id": "source-ground", "board_id": "source", "kind": "ground_reference", "net": "0"},
                {"id": "load-ground", "board_id": "load", "kind": "ground_reference", "net": "0"},
                {"id": "load-raw-source", "board_id": "load", "kind": "voltage_source",
                 "net": "raw", "reference_net": "0", "refdes": "VFIX",
                 "model": f"PULSE(0 {raw_high:g} 0 1n 1n 40u 80u)"},
            ]
        return [
            {"id": "source-ctrl", "board_id": "source", "kind": "observation", "net": "ctrl"},
            {"id": "load-ctrl", "board_id": "load", "kind": "observation", "net": "ctrl"},
            {"id": "load-filt", "board_id": "load", "kind": "observation", "net": "filt"},
            {"id": "source-ground", "board_id": "source", "kind": "ground_reference", "net": "0"},
            {"id": "load-ground", "board_id": "load", "kind": "ground_reference", "net": "0"},
            {"id": "load-ctrl-source", "board_id": "load", "kind": "voltage_source",
             "net": "ctrl", "reference_net": "0", "refdes": "VFIXCTRL",
             "model": "PULSE(0 5 0 1n 1n 40u 80u)"},
        ]

    def continuous_response_contracts(self) -> dict[str, ContinuousResponseContract]:
        tolerance = 0.05 if self.source_kind == "G" else 0.02
        return {
            "filt": ContinuousResponseContract(
                final_value_tolerance=tolerance,
                extrema_value_tolerance=tolerance,
                response_range_tolerance=tolerance,
                time_tolerance=1e-5,
                minimum_samples=8,
            )
        }

    def build(self) -> tuple[CircuitDesign, dict[str, Any], list[dict[str, Any]]]:
        raw_components = self.source_components()
        design = CircuitDesign(
            design_id=f"{self.source_kind.lower()}-rc-split-v1",
            title=f"{self.source_kind} controlled source to RC load",
            components=tuple(
                CircuitComponent.from_dict({key: value for key, value in item.items() if key != "board"})
                for item in raw_components
            ),
            nets=tuple(sorted({node for item in raw_components for node in item["nodes"]})),
        )
        partition = plan_multiboard_partition(
            raw_components, self.boards(), [self.connector()]
        )
        if partition.get("feasible") is not True:
            raise ValueError(f"{self.case_id}: partition is infeasible: {partition.get('violations', [])}")
        return design, partition, copy.deepcopy(self.fixtures())

    def manifest(self) -> dict[str, Any]:
        design, partition, fixtures = self.build()
        return {
            "schema_version": 1, "case_id": self.case_id, "description": self.description,
            "target_version": self.target_version, "analysis": self.analysis,
            "analysis_options": copy.deepcopy(self.analysis_options),
            "source_kind": self.source_kind,
            "source_value": self.source_value,
            "series_resistance": self.series_resistance,
            "filter_capacitance": self.filter_capacitance,
            "load_resistance": self.load_resistance,
            "design_id": design.design_id, "partition_digest": hashlib.sha256(
                json.dumps(partition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "connector": self.connector(), "fixtures": fixtures,
        }


@dataclass(frozen=True)
class Adc1Dac1MultiboardRegressionCase:
    """A bounded one-bit ADC-to-DAC boundary across two boards.

    The converter models are ordinary ``BV`` behavioural sources. They
    exercise the portable adapter semantics without claiming vendor ADC/DAC
    accuracy, sampling, protection, or production suitability. The DAC board
    receives an equivalent ADC fixture so its isolated run can be compared
    with the complete reference design.
    """

    case_id: str = "adc1_dac1_split"
    description: str = "One-bit ADC source board driving a one-bit DAC and RC load board through HDR1X4."
    target_version: str = "14.3"
    analysis: str = "tran"
    analysis_options: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.analysis_options is None:
            object.__setattr__(self, "analysis_options", {
                "sample_rate": 1_000_000.0, "num_samples": 200,
                "duration": 0.0001, "max_points": 2000,
                "series_alignment": "linear",
            })

    @staticmethod
    def _threshold_expression(analog: str = "analog", high: str = "high", low: str = "0") -> str:
        return f"V={{if(V({analog})>(V({low})+(V({high})-V({low}))*0.5),V({high}),V({low}))}}"

    @staticmethod
    def _dac_expression(digital: str = "digital", high: str = "high", low: str = "0") -> str:
        return f"V={{if(V({digital})>((V({high})+V({low}))/2),V({high}),V({low}))}}"

    def source_components(self) -> list[dict[str, Any]]:
        return [
            {"refdes": "VDD", "kind": "V", "nodes": ["high", "0"], "value": "5", "board": "adc"},
            {"refdes": "VAN", "kind": "V", "nodes": ["analog", "0"],
             "model": "DC 5", "board": "adc"},
            {"refdes": "BADC", "kind": "BV", "nodes": ["digital", "0"],
             "model": self._threshold_expression(), "board": "adc"},
            {"refdes": "RANALOG", "kind": "R", "nodes": ["analog", "0"], "value": "1G", "board": "adc"},
            {"refdes": "RHIGH", "kind": "R", "nodes": ["high", "0"], "value": "1G", "board": "adc"},
            {"refdes": "BDAC", "kind": "BV", "nodes": ["analog_out", "0"],
             "model": self._dac_expression(), "board": "dac"},
            {"refdes": "RDIGITAL", "kind": "R", "nodes": ["digital", "0"], "value": "1G", "board": "dac"},
            {"refdes": "RHIGH_DAC", "kind": "R", "nodes": ["high", "0"], "value": "1G", "board": "dac"},
            {"refdes": "R1", "kind": "R", "nodes": ["analog_out", "filt"], "value": "1k", "board": "dac"},
            {"refdes": "C1", "kind": "C", "nodes": ["filt", "0"], "value": "1u", "board": "dac"},
            {"refdes": "RLOAD", "kind": "R", "nodes": ["filt", "0"], "value": "100k", "board": "dac"},
        ]

    def boards(self) -> list[dict[str, Any]]:
        return [
            {"id": "adc", "role": "adc-source", "max_components": 8, "max_connector_pins": 4},
            {"id": "dac", "role": "dac-rc-load", "max_components": 8, "max_connector_pins": 4},
        ]

    def connector(self) -> dict[str, Any]:
        return {
            "id": "JLINK", "part": "HDR1X4", "boards": ["adc", "dac"],
            "instances": [{"board": "adc", "refdes": "J1"}, {"board": "dac", "refdes": "J1"}],
            "pins": [
                {"number": 1, "net": "digital", "signal_type": "digital", "direction": "bidirectional",
                 "board_directions": {"adc": "out", "dac": "in"}},
                {"number": 2, "net": "high", "signal_type": "power", "direction": "power",
                 "board_directions": {"adc": "out", "dac": "in"}},
                {"number": 3, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 4, "net": "0", "signal_type": "ground", "direction": "passive"},
            ],
        }

    def digital_fixture(self) -> dict[str, Any]:
        # ``analog_fix`` is private to the fixture; only the declared ports
        # are allowed to attach to the DAC board.
        components = [
            {"refdes": "VFIXA", "kind": "V", "nodes": ["analog_fix", "0"],
             "model": "DC 5"},
            {"refdes": "BFIXD", "kind": "BV", "nodes": ["digital", "0"],
             "model": self._threshold_expression("analog_fix")},
            {"refdes": "RFIXA", "kind": "R", "nodes": ["analog_fix", "0"], "value": "1G"},
            {"refdes": "RFIXHIGH", "kind": "R", "nodes": ["high", "0"], "value": "1G"},
        ]
        ports = ["digital", "high", "0"]
        return {
            "id": "dac-digital-fixture", "board_id": "dac", "kind": "circuit_fragment",
            "net": "digital", "ports": ports,
            "source_compiled_digest": _fixture_digest(components, ports),
            "model_scope": "explicit-digital-fixture", "components": components,
        }

    def fixtures(self) -> list[dict[str, Any]]:
        return [
            {"id": "adc-digital", "board_id": "adc", "kind": "observation", "net": "digital"},
            {"id": "adc-analog", "board_id": "adc", "kind": "observation", "net": "analog"},
            {"id": "adc-high", "board_id": "adc", "kind": "observation", "net": "high"},
            {"id": "adc-ground", "board_id": "adc", "kind": "ground_reference", "net": "0"},
            {"id": "dac-digital", "board_id": "dac", "kind": "observation", "net": "digital"},
            {"id": "dac-high", "board_id": "dac", "kind": "observation", "net": "high"},
            {"id": "dac-filt", "board_id": "dac", "kind": "observation", "net": "filt"},
            {"id": "dac-ground", "board_id": "dac", "kind": "ground_reference", "net": "0"},
            {"id": "dac-high-source", "board_id": "dac", "kind": "voltage_source",
             "net": "high", "reference_net": "0", "refdes": "VHIGH", "value": "5"},
            self.digital_fixture(),
        ]

    def build(self) -> tuple[CircuitDesign, dict[str, Any], list[dict[str, Any]]]:
        raw_components = self.source_components()
        design = CircuitDesign(
            design_id="adc1-dac1-split-v1", title="One-bit ADC to DAC with RC load",
            components=tuple(
                CircuitComponent.from_dict({key: value for key, value in item.items() if key != "board"})
                for item in raw_components
            ),
            nets=tuple(sorted({node for item in raw_components for node in item["nodes"]})),
        )
        partition = plan_multiboard_partition(raw_components, self.boards(), [self.connector()])
        if partition.get("feasible") is not True:
            raise ValueError(f"{self.case_id}: partition is infeasible: {partition.get('violations', [])}")
        return design, partition, copy.deepcopy(self.fixtures())

    def manifest(self) -> dict[str, Any]:
        design, partition, fixtures = self.build()
        return {
            "schema_version": 1, "case_id": self.case_id, "description": self.description,
            "target_version": self.target_version, "analysis": self.analysis,
            "analysis_options": copy.deepcopy(self.analysis_options),
            "design_id": design.design_id, "partition_digest": hashlib.sha256(
                json.dumps(partition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "fixture_digest": next(item["source_compiled_digest"] for item in fixtures if item["kind"] == "circuit_fragment"),
            "connector": self.connector(), "fixtures": fixtures,
        }


@dataclass(frozen=True)
class Adc1Dac1EdgeMultiboardRegressionCase(Adc1Dac1MultiboardRegressionCase):
    """ADC1/DAC1 transition case gated by threshold-edge timing."""

    case_id: str = "adc1_dac1_edge_split"
    description: str = "One-bit ADC to DAC transition case with adaptive-axis edge verification."

    def source_components(self) -> list[dict[str, Any]]:
        components = super().source_components()
        for component in components:
            if component["refdes"] == "VAN":
                component["model"] = "PULSE(0 5 0 1n 1n 20u 40u)"
        return components

    def digital_fixture(self) -> dict[str, Any]:
        fixture = super().digital_fixture()
        fixture["components"][0]["model"] = "PULSE(0 5 0 1n 1n 20u 40u)"
        fixture["source_compiled_digest"] = _fixture_digest(
            fixture["components"], fixture["ports"]
        )
        return fixture

    def digital_edge_contracts(self) -> dict[str, DigitalEdgeComparisonContract]:
        return {
            "digital": DigitalEdgeComparisonContract(
                threshold_voltage=2.5,
                level_tolerance=0.05,
                edge_time_tolerance=1e-9,
            )
        }


@dataclass(frozen=True)
class Adc4Dac4MultiboardRegressionCase:
    """A bounded four-bit ADC/DAC bridge using two verified four-pin headers."""

    case_id: str = "adc4_dac4_split"
    description: str = "Four-bit ADC source board driving a four-bit DAC and RC load board through two HDR1X4 connectors."
    target_version: str = "14.3"
    analysis: str = "tran"
    analysis_options: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.analysis_options is None:
            object.__setattr__(self, "analysis_options", {
                "sample_rate": 1_000_000.0, "num_samples": 200,
                "duration": 0.0001, "max_points": 2000,
                "series_alignment": "linear",
            })

    @staticmethod
    def _adapter_components(kind: str, *, prefix: str, analog: str, data_nodes: tuple[str, ...]) -> list[dict[str, Any]]:
        """Expand the reviewed portable adapter and assign stable refdes names."""
        from .schematic_builder import parse_netlist

        if kind == "ADC4":
            text = f"XADAPTER {analog} {' '.join(data_nodes)} high 0 @ADC4\n.end\n"
        elif kind == "DAC4":
            text = f"XADAPTER {' '.join(data_nodes)} {analog} high 0 @DAC4\n.end\n"
        else:
            raise ValueError(f"unsupported four-bit adapter: {kind}")
        parsed = parse_netlist(text)
        if parsed.unsupported:
            raise ValueError(f"{kind} adapter expansion is unsupported: {parsed.unsupported}")
        result: list[dict[str, Any]] = []
        for component in parsed.components:
            nodes = list(component.nodes)
            if component.kind == "BV":
                output = nodes[0]
                refdes = f"B{prefix}{output.upper()}"
            elif component.kind == "R":
                first = nodes[0].upper()
                if first in {node.upper() for node in data_nodes}:
                    refdes = f"R{prefix}{first}"
                elif first == analog.upper():
                    refdes = f"R{prefix}ANALOG"
                elif first == "HIGH":
                    refdes = f"R{prefix}HIGH"
                else:
                    raise ValueError(f"{kind} adapter produced an unexpected anchor: {nodes}")
            else:
                raise ValueError(f"{kind} adapter produced unsupported component {component.kind}")
            result.append({
                "refdes": refdes, "kind": component.kind, "nodes": nodes,
                "value": component.value, "model": component.model,
            })
        return result

    def source_components(self) -> list[dict[str, Any]]:
        bits = ("d0", "d1", "d2", "d3")
        adc = self._adapter_components("ADC4", prefix="ADC", analog="analog", data_nodes=bits)
        dac = self._adapter_components("DAC4", prefix="DAC", analog="analog_out", data_nodes=bits)
        return [
            {"refdes": "VDD", "kind": "V", "nodes": ["high", "0"], "value": "5", "board": "adc"},
            {"refdes": "VAN", "kind": "V", "nodes": ["analog", "0"], "model": "DC 5", "board": "adc"},
            *[dict(item, board="adc") for item in adc],
            *[dict(item, board="dac") for item in dac],
            {"refdes": "R1", "kind": "R", "nodes": ["analog_out", "filt"], "value": "1k", "board": "dac"},
            {"refdes": "C1", "kind": "C", "nodes": ["filt", "0"], "value": "1u", "board": "dac"},
            {"refdes": "RLOAD", "kind": "R", "nodes": ["filt", "0"], "value": "100k", "board": "dac"},
        ]

    def boards(self) -> list[dict[str, Any]]:
        return [
            {"id": "adc", "role": "adc4-source", "max_components": 10, "max_connector_pins": 8},
            {"id": "dac", "role": "dac4-rc-load", "max_components": 10, "max_connector_pins": 8},
        ]

    def connector(self) -> dict[str, Any]:
        return {
            "id": "JDATA", "part": "HDR1X4", "boards": ["adc", "dac"],
            "instances": [{"board": "adc", "refdes": "JDATA"}, {"board": "dac", "refdes": "JDATA"}],
            "pins": [
                {"number": index + 1, "net": bit, "signal_type": "digital", "direction": "bidirectional",
                 "board_directions": {"adc": "out", "dac": "in"}}
                for index, bit in enumerate(("d0", "d1", "d2", "d3"))
            ],
        }

    def power_connector(self) -> dict[str, Any]:
        return {
            "id": "JPOWER", "part": "HDR1X4", "boards": ["adc", "dac"],
            "instances": [{"board": "adc", "refdes": "JPOWER"}, {"board": "dac", "refdes": "JPOWER"}],
            "pins": [
                {"number": 1, "net": "high", "signal_type": "power", "direction": "power",
                 "board_directions": {"adc": "out", "dac": "in"}},
                {"number": 2, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 3, "net": "0", "signal_type": "ground", "direction": "passive"},
                {"number": 4, "net": "0", "signal_type": "ground", "direction": "passive"},
            ],
        }

    def digital_fixture(self) -> dict[str, Any]:
        bits = ("d0", "d1", "d2", "d3")
        components = [
            {"refdes": "VFIXA", "kind": "V", "nodes": ["analog_fix", "0"], "model": "DC 5"},
            *self._adapter_components("ADC4", prefix="FIXD", analog="analog_fix", data_nodes=bits),
        ]
        ports = [*bits, "high", "0"]
        return {
            "id": "dac4-digital-fixture", "board_id": "dac", "kind": "circuit_fragment",
            "net": "d0", "ports": ports,
            "source_compiled_digest": _fixture_digest(components, ports),
            "model_scope": "explicit-digital-fixture", "components": components,
        }

    def fixtures(self) -> list[dict[str, Any]]:
        bits = ("d0", "d1", "d2", "d3")
        return [
            *[{"id": f"adc-{bit}", "board_id": "adc", "kind": "observation", "net": bit} for bit in bits],
            {"id": "adc-analog", "board_id": "adc", "kind": "observation", "net": "analog"},
            {"id": "adc-high", "board_id": "adc", "kind": "observation", "net": "high"},
            {"id": "adc-ground", "board_id": "adc", "kind": "ground_reference", "net": "0"},
            *[{"id": f"dac-{bit}", "board_id": "dac", "kind": "observation", "net": bit} for bit in bits],
            {"id": "dac-high", "board_id": "dac", "kind": "observation", "net": "high"},
            {"id": "dac-analog", "board_id": "dac", "kind": "observation", "net": "analog_out"},
            {"id": "dac-filt", "board_id": "dac", "kind": "observation", "net": "filt"},
            {"id": "dac-ground", "board_id": "dac", "kind": "ground_reference", "net": "0"},
            {"id": "dac-high-source", "board_id": "dac", "kind": "voltage_source",
             "net": "high", "reference_net": "0", "refdes": "VHIGH", "value": "5"},
            self.digital_fixture(),
        ]

    def build(self) -> tuple[CircuitDesign, dict[str, Any], list[dict[str, Any]]]:
        raw_components = self.source_components()
        design = CircuitDesign(
            design_id="adc4-dac4-split-v1", title="Four-bit ADC to DAC with RC load",
            components=tuple(
                CircuitComponent.from_dict({key: value for key, value in item.items() if key != "board"})
                for item in raw_components
            ),
            nets=tuple(sorted({node for item in raw_components for node in item["nodes"]})),
        )
        partition = plan_multiboard_partition(
            raw_components, self.boards(), [self.connector(), self.power_connector()]
        )
        if partition.get("feasible") is not True:
            raise ValueError(f"{self.case_id}: partition is infeasible: {partition.get('violations', [])}")
        return design, partition, copy.deepcopy(self.fixtures())

    def manifest(self) -> dict[str, Any]:
        design, partition, fixtures = self.build()
        return {
            "schema_version": 1, "case_id": self.case_id, "description": self.description,
            "target_version": self.target_version, "analysis": self.analysis,
            "analysis_options": copy.deepcopy(self.analysis_options),
            "design_id": design.design_id, "partition_digest": hashlib.sha256(
                json.dumps(partition, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "fixture_digest": next(item["source_compiled_digest"] for item in fixtures if item["kind"] == "circuit_fragment"),
            "connectors": [self.connector(), self.power_connector()], "fixtures": fixtures,
        }


@dataclass(frozen=True)
class Adc4Dac4EdgeMultiboardRegressionCase(Adc4Dac4MultiboardRegressionCase):
    """Four-bit transition case gated by four digital edge contracts."""

    case_id: str = "adc4_dac4_edge_split"
    description: str = "Four-bit ADC to DAC transition case with per-bit adaptive-axis edge verification."

    def source_components(self) -> list[dict[str, Any]]:
        components = super().source_components()
        for component in components:
            if component["refdes"] == "VAN":
                component["model"] = "PULSE(0 5 0 160u 160u 10u 340u)"
        return components

    def digital_fixture(self) -> dict[str, Any]:
        fixture = super().digital_fixture()
        fixture["components"][0]["model"] = "PULSE(0 5 0 160u 160u 10u 340u)"
        fixture["source_compiled_digest"] = _fixture_digest(
            fixture["components"], fixture["ports"]
        )
        return fixture

    def fixtures(self) -> list[dict[str, Any]]:
        # Keep the raw DAC output outside this event contract, but observe the
        # declared RC load through its independent continuous-response gate.
        return [
            item for item in super().fixtures()
            if item.get("id") != "dac-analog"
        ]

    def digital_edge_contracts(self) -> dict[str, DigitalEdgeComparisonContract]:
        return {
            bit: DigitalEdgeComparisonContract(
                threshold_voltage=2.5,
                level_tolerance=0.05,
                edge_time_tolerance=1e-7,
            )
            for bit in ("d0", "d1", "d2", "d3")
        }

    def continuous_response_contracts(self) -> dict[str, ContinuousResponseContract]:
        return {
            "filt": ContinuousResponseContract(
                final_value_tolerance=0.02,
                extrema_value_tolerance=0.02,
                response_range_tolerance=0.02,
                time_tolerance=1e-5,
                minimum_samples=8,
            )
        }


@dataclass(frozen=True)
class Adc4Dac4TransferMultiboardRegressionCase(Adc4Dac4MultiboardRegressionCase):
    """Sixteen-code ADC/DAC transfer contract on the complete reference."""

    case_id: str = "adc4_dac4_transfer_split"
    description: str = "Four-bit ADC/DAC transfer case with 16-code reconstruction evidence."
    analysis_options: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.analysis_options is None:
            object.__setattr__(self, "analysis_options", {
                "sample_rate": 1_000_000.0, "num_samples": 400,
                "duration": 0.00034, "max_points": 2000,
                "series_alignment": "linear",
            })

    def source_components(self) -> list[dict[str, Any]]:
        components = super().source_components()
        for component in components:
            if component["refdes"] == "VAN":
                component["model"] = "PULSE(0 5 0 160u 160u 10u 340u)"
        return components

    def digital_fixture(self) -> dict[str, Any]:
        fixture = super().digital_fixture()
        fixture["components"][0]["model"] = "PULSE(0 5 0 160u 160u 10u 340u)"
        fixture["source_compiled_digest"] = _fixture_digest(
            fixture["components"], fixture["ports"]
        )
        return fixture

    def fixtures(self) -> list[dict[str, Any]]:
        # The converter output and RC output are separate contracts: the
        # former checks code reconstruction, while the latter checks the
        # continuous response of the declared load network.
        return super().fixtures()

    def digital_edge_contracts(self) -> dict[str, DigitalEdgeComparisonContract]:
        return {
            bit: DigitalEdgeComparisonContract(
                threshold_voltage=2.5,
                level_tolerance=0.05,
                # The slow 160 us input ramp is solved on independently
                # adaptive axes; allow the measured threshold-time drift while
                # the 16-code transfer contract remains exact at each sample.
                edge_time_tolerance=1e-5,
            )
            for bit in ("d0", "d1", "d2", "d3")
        }

    def converter_transfer_contract(self) -> ConverterTransferContract:
        return ConverterTransferContract(
            analog_input="analog",
            digital_bits=("d0", "d1", "d2", "d3"),
            analog_output="analog_out",
            low_voltage=0.0,
            high_voltage=5.0,
            voltage_tolerance=0.02,
            boundary_guard_voltage=0.01,
        )

    def continuous_response_contracts(self) -> dict[str, ContinuousResponseContract]:
        return {
            "filt": ContinuousResponseContract(
                final_value_tolerance=0.02,
                extrema_value_tolerance=0.02,
                response_range_tolerance=0.02,
                # Independent native transient runs use adaptive axes. The
                # declared bound is checked at the analysis endpoints and at
                # the observed peak/valley times.
                time_tolerance=1e-5,
                minimum_samples=8,
            )
        }


def controlled_source_multiboard_corner_cases() -> tuple[ControlledSourceMultiboardRegressionCase, ...]:
    """Return bounded gain/transconductance corners for native reruns."""
    return (
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split_gain05",
            source_kind="E",
            source_value="0.5",
            description="VCVS gain 0.5 corner driving an RC load board.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split_gain2",
            source_kind="E",
            source_value="2",
            description="VCVS gain 2 corner driving an RC load board.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split_g05m",
            source_kind="G",
            source_value="0.5m",
            description="VCCS 0.5 mS corner driving an RC load board.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split_g2m",
            source_kind="G",
            source_value="2m",
            description="VCCS 2 mS corner driving an RC load board.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split_r500",
            source_kind="E",
            series_resistance="500",
            description="VCVS with 500 Ohm series RC resistance corner.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split_c2u",
            source_kind="E",
            filter_capacitance="2u",
            description="VCVS with 2 uF filter capacitance corner.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split_r2k",
            source_kind="G",
            series_resistance="2k",
            description="VCCS with 2 kOhm series RC resistance corner.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split_load50k",
            source_kind="G",
            load_resistance="50k",
            description="VCCS with 50 kOhm parallel load corner.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split_combo_low",
            source_kind="E",
            source_value="0.5",
            series_resistance="500",
            filter_capacitance="2u",
            description="VCVS low-gain combined low-R/high-C corner.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split_combo_high",
            source_kind="G",
            source_value="2m",
            series_resistance="2k",
            load_resistance="50k",
            description="VCCS high-transconductance combined high-R/low-load corner.",
        ),
    )


def hybrid_multiboard_regression_cases() -> tuple[
    HybridMultiboardRegressionCase | Adc1Dac1MultiboardRegressionCase
    | Adc1Dac1EdgeMultiboardRegressionCase | Adc4Dac4MultiboardRegressionCase
    | Adc4Dac4EdgeMultiboardRegressionCase | Adc4Dac4TransferMultiboardRegressionCase
    | ControlledSourceMultiboardRegressionCase,
    ...,
]:
    return (
        HybridMultiboardRegressionCase(
            case_id="dnot4_rc_split",
            description="DNOT4 digital source board driving an RC load board through HDR1X4.",
            target_version="14.3", analysis="tran",
            analysis_options={
                "sample_rate": 1_000_000.0, "num_samples": 200, "duration": 0.0001,
                "max_points": 2000, "series_alignment": "linear",
            },
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vcvs_rc_split",
            source_kind="E",
            description="VCVS source board driving an RC load board through HDR1X4.",
        ),
        ControlledSourceMultiboardRegressionCase(
            case_id="vccs_rc_split",
            source_kind="G",
            description="VCCS source board driving an RC load board through HDR1X4.",
        ),
        *controlled_source_multiboard_corner_cases(),
        Adc1Dac1MultiboardRegressionCase(),
        Adc1Dac1EdgeMultiboardRegressionCase(),
        Adc4Dac4MultiboardRegressionCase(),
        Adc4Dac4EdgeMultiboardRegressionCase(),
        Adc4Dac4TransferMultiboardRegressionCase(),
    )


def select_hybrid_multiboard_regression_case(case_id: str | None = None) -> (
    HybridMultiboardRegressionCase | Adc1Dac1MultiboardRegressionCase
    | Adc1Dac1EdgeMultiboardRegressionCase | Adc4Dac4MultiboardRegressionCase
    | Adc4Dac4EdgeMultiboardRegressionCase | Adc4Dac4TransferMultiboardRegressionCase
    | ControlledSourceMultiboardRegressionCase
):
    cases = hybrid_multiboard_regression_cases()
    if case_id is None:
        return cases[0]
    for case in cases:
        if case.case_id == case_id:
            return case
    raise ValueError(f"unknown hybrid multi-board regression case: {case_id!r}")


__all__ = [
    "HybridMultiboardRegressionCase",
    "ControlledSourceMultiboardRegressionCase",
    "controlled_source_multiboard_corner_cases",
    "Adc1Dac1MultiboardRegressionCase",
    "Adc1Dac1EdgeMultiboardRegressionCase",
    "Adc4Dac4MultiboardRegressionCase",
    "Adc4Dac4EdgeMultiboardRegressionCase",
    "Adc4Dac4TransferMultiboardRegressionCase",
    "hybrid_multiboard_regression_cases",
    "select_hybrid_multiboard_regression_case",
]
