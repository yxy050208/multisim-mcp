"""Public, declarative component adapters for portable Multisim netlists.

Adapters deliberately expand to ordinary SPICE primitives.  This keeps the
open-source package independent of NI component databases and makes the same
netlist executable by Multisim's command-line simulator.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Mapping


_NAME_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_NODE_RE: Final = re.compile(r"^[A-Za-z0-9_.$:+-]{1,128}$")
_PLACEHOLDER_RE: Final = re.compile(r"\{([A-Za-z][A-Za-z0-9_]*)\}")
_MAX_PACK_BYTES: Final = 256 * 1024
_MAX_EXPANDED_BYTES: Final = 4 * 1024 * 1024
_MAX_INVOCATIONS: Final = 1000


@dataclass(frozen=True)
class ParameterDefinition:
    """One finite numeric parameter accepted by an adapter."""

    name: str
    default: float
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class ComponentAdapter:
    """A safe macro that expands a pseudo-component to SPICE lines."""

    kind: str
    terminals: tuple[str, ...]
    parameters: tuple[ParameterDefinition, ...]
    expansion: tuple[str, ...]
    description_zh: str
    description_en: str
    maturity: str = "portable-model"
    source: str = "built-in"


def _p(
    name: str,
    default: float,
    minimum: float | None = None,
    maximum: float | None = None,
) -> ParameterDefinition:
    return ParameterDefinition(name, default, minimum, maximum)


BUILTIN_ADAPTERS: Final[dict[str, ComponentAdapter]] = {
    "TRANSFORMER": ComponentAdapter(
        "TRANSFORMER", ("p1", "p2", "s1", "s2"),
        (_p("LP", 1e-3, 1e-15), _p("LS", 1e-3, 1e-15), _p("K", 0.999, -1, 1)),
        ("L{stem}P {p1} {p2} {LP}", "L{stem}S {s1} {s2} {LS}", "K{stem} L{stem}P L{stem}S {K}"),
        "线性双绕组变压器（耦合电感等效）", "Linear two-winding transformer (coupled-inductor model)",
    ),
    "POTENTIOMETER": ComponentAdapter(
        "POTENTIOMETER", ("high", "wiper", "low"),
        (_p("R", 10_000, 1e-12), _p("POSITION", 0.5, 0, 1), _p("RMIN", 1e-6, 1e-15)),
        ("R{stem}A {high} {wiper} {RA}", "R{stem}B {wiper} {low} {RB}"),
        "三端线性电位器", "Three-terminal linear potentiometer",
    ),
    "RELAY": ComponentAdapter(
        "RELAY", ("coil_p", "coil_n", "contact_a", "contact_b"),
        (_p("RCOIL", 400, 1e-12), _p("RON", 0.05, 1e-12), _p("ROFF", 1e9, 1), _p("VON", 3, 0), _p("VOFF", 1, 0)),
        ("R{stem}COIL {coil_p} {coil_n} {RCOIL}", "S{stem} {contact_a} {contact_b} {coil_p} {coil_n} M{stem}_RELAY", ".model M{stem}_RELAY SW(RON={RON} ROFF={ROFF} VON={VON} VOFF={VOFF})"),
        "电压驱动常开继电器等效模型", "Voltage-driven normally-open relay model",
    ),
    "CRYSTAL": ComponentAdapter(
        "CRYSTAL", ("p", "n"),
        (_p("RM", 20, 1e-12), _p("LM", 0.02, 1e-15), _p("CM", 20e-15, 1e-18), _p("C0", 3e-12, 1e-18)),
        ("R{stem}M {p} n_{stem}_r {RM}", "L{stem}M n_{stem}_r n_{stem}_l {LM}", "C{stem}M n_{stem}_l {n} {CM}", "C{stem}0 {p} {n} {C0}"),
        "Butterworth-Van Dyke 晶振等效模型", "Butterworth-Van Dyke crystal model",
    ),
    "POWER_DIODE": ComponentAdapter(
        "POWER_DIODE", ("anode", "cathode"),
        (_p("IS", 1e-9, 1e-30), _p("N", 1.5, 0.1, 10), _p("RS", 0.02, 0), _p("BV", 100, 0.01), _p("IBV", 1e-6, 1e-30)),
        ("D{stem} {anode} {cathode} M{stem}_PD", ".model M{stem}_PD D(IS={IS} N={N} RS={RS} BV={BV} IBV={IBV})"),
        "通用功率二极管", "Generic power diode",
    ),
    "POWER_NMOS": ComponentAdapter(
        "POWER_NMOS", ("drain", "gate", "source"),
        (_p("VTO", 3, -100, 100), _p("KP", 10, 1e-15), _p("LAMBDA", 0.01, 0), _p("RD", 0.02, 0), _p("RS", 0.02, 0)),
        ("M{stem} {drain} {gate} {source} {source} M{stem}_NMOS", ".model M{stem}_NMOS NMOS(VTO={VTO} KP={KP} LAMBDA={LAMBDA} RD={RD} RS={RS})"),
        "通用增强型功率 NMOS", "Generic enhancement-mode power NMOS",
    ),
    "POWER_PMOS": ComponentAdapter(
        "POWER_PMOS", ("drain", "gate", "source"),
        (_p("VTO", -3, -100, 100), _p("KP", 10, 1e-15), _p("LAMBDA", 0.01, 0), _p("RD", 0.02, 0), _p("RS", 0.02, 0)),
        ("M{stem} {drain} {gate} {source} {source} M{stem}_PMOS", ".model M{stem}_PMOS PMOS(VTO={VTO} KP={KP} LAMBDA={LAMBDA} RD={RD} RS={RS})"),
        "通用增强型功率 PMOS", "Generic enhancement-mode power PMOS",
    ),
    "DFF": ComponentAdapter(
        "DFF", ("d", "clk", "set", "reset", "q", "qbar", "high", "low"), (),
        ("A{stem}INV {d} n_{stem}_dbar {high} {low} NOT", "A{stem}JK {d} n_{stem}_dbar {clk} {set} {reset} {q} {qbar} JK"),
        "由 JK 触发器构成的 D 触发器", "D flip-flop synthesized from a JK flip-flop",
    ),
    "TFF": ComponentAdapter(
        "TFF", ("t", "clk", "set", "reset", "q", "qbar", "high", "low"), (),
        ("A{stem}JK {t} {t} {clk} {set} {reset} {q} {qbar} JK",),
        "由 JK 触发器构成的 T 触发器", "T flip-flop synthesized from a JK flip-flop",
    ),
    "COUNTER4": ComponentAdapter(
        "COUNTER4", ("clk", "reset", "q0", "q1", "q2", "q3", "high", "low"), (),
        ("A{stem}0 {high} {high} {clk} {low} {reset} {q0} n_{stem}_qb0 JK", "A{stem}1 {high} {high} n_{stem}_qb0 {low} {reset} {q1} n_{stem}_qb1 JK", "A{stem}2 {high} {high} n_{stem}_qb1 {low} {reset} {q2} n_{stem}_qb2 JK", "A{stem}3 {high} {high} n_{stem}_qb2 {low} {reset} {q3} n_{stem}_qb3 JK"),
        "四位异步二进制计数器", "Four-bit asynchronous binary counter",
    ),
    "SHIFT_REGISTER4": ComponentAdapter(
        "SHIFT_REGISTER4", ("data", "clk", "reset", "q0", "q1", "q2", "q3", "high", "low"), (),
        ("A{stem}I0 {data} n_{stem}_d0b {high} {low} NOT", "A{stem}F0 {data} n_{stem}_d0b {clk} {low} {reset} {q0} n_{stem}_q0b JK", "A{stem}I1 {q0} n_{stem}_d1b {high} {low} NOT", "A{stem}F1 {q0} n_{stem}_d1b {clk} {low} {reset} {q1} n_{stem}_q1b JK", "A{stem}I2 {q1} n_{stem}_d2b {high} {low} NOT", "A{stem}F2 {q1} n_{stem}_d2b {clk} {low} {reset} {q2} n_{stem}_q2b JK", "A{stem}I3 {q2} n_{stem}_d3b {high} {low} NOT", "A{stem}F3 {q2} n_{stem}_d3b {clk} {low} {reset} {q3} n_{stem}_q3b JK"),
        "四位串入并出移位寄存器", "Four-bit serial-in/parallel-out shift register",
    ),
    "ADC1": ComponentAdapter(
        "ADC1", ("analog", "digital", "high", "low"), (_p("THRESHOLD", 0.5, 0, 1),),
        ("B{stem}ADC {digital} {low} V={if(V({analog})>(V({low})+(V({high})-V({low}))*{THRESHOLD}),V({high}),V({low}))}",),
        "单比特模数桥", "One-bit analog-to-digital bridge",
    ),
    "DAC1": ComponentAdapter(
        "DAC1", ("digital", "analog", "high", "low"), (),
        ("B{stem}DAC {analog} {low} V={if(V({digital})>((V({high})+V({low}))/2),V({high}),V({low}))}",),
        "单比特数模桥", "One-bit digital-to-analog bridge",
    ),
}


def _number(value: Any, label: str) -> float:
    try:
        if isinstance(value, str):
            match = re.fullmatch(
                r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
                r"(meg|mil|[tgkmunpf])?",
                value.strip(),
                re.IGNORECASE,
            )
            if not match:
                raise ValueError
            scale = {
                "": 1.0, "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
                "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12,
                "f": 1e-15, "mil": 25.4e-6,
            }[match.group(2).lower() if match.group(2) else ""]
            number = float(match.group(1)) * scale
        else:
            number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def _validate_adapter(adapter: ComponentAdapter) -> None:
    if not _NAME_RE.fullmatch(adapter.kind):
        raise ValueError("adapter kind must be a safe identifier")
    if not adapter.terminals or len(adapter.terminals) > 32:
        raise ValueError(f"{adapter.kind}: terminals must contain 1-32 entries")
    if not adapter.expansion or len(adapter.expansion) > 64:
        raise ValueError(f"{adapter.kind}: expansion must contain 1-64 lines")
    computed = {"RA", "RB"} if adapter.kind == "POTENTIOMETER" else set()
    raw_names = [
        "stem",
        *adapter.terminals,
        *(item.name for item in adapter.parameters),
        *computed,
    ]
    if len({name.casefold() for name in raw_names}) != len(raw_names):
        raise ValueError(f"{adapter.kind}: duplicate terminal or parameter name")
    names = set(raw_names)
    for name in raw_names:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"{adapter.kind}: unsafe placeholder name {name!r}")
    for parameter in adapter.parameters:
        if not math.isfinite(parameter.default):
            raise ValueError(f"{adapter.kind}: parameter defaults must be finite")
        if parameter.minimum is not None and not math.isfinite(parameter.minimum):
            raise ValueError(f"{adapter.kind}: parameter minima must be finite")
        if parameter.maximum is not None and not math.isfinite(parameter.maximum):
            raise ValueError(f"{adapter.kind}: parameter maxima must be finite")
        if (
            parameter.minimum is not None
            and parameter.maximum is not None
            and parameter.minimum > parameter.maximum
        ):
            raise ValueError(f"{adapter.kind}: parameter minimum exceeds maximum")
        if parameter.minimum is not None and parameter.default < parameter.minimum:
            raise ValueError(f"{adapter.kind}: parameter default is below minimum")
        if parameter.maximum is not None and parameter.default > parameter.maximum:
            raise ValueError(f"{adapter.kind}: parameter default is above maximum")
    if not adapter.description_zh.strip() or not adapter.description_en.strip():
        raise ValueError(f"{adapter.kind}: bilingual descriptions are required")
    for line in adapter.expansion:
        if not line or len(line) > 2048 or "\n" in line or "\r" in line:
            raise ValueError(f"{adapter.kind}: each expansion must be one bounded line")
        missing = set(_PLACEHOLDER_RE.findall(line)) - names
        if missing:
            raise ValueError(f"{adapter.kind}: unknown placeholders: {sorted(missing)}")
        first = line.lstrip().split(maxsplit=1)[0].upper()
        if not (first.startswith(("R", "C", "L", "K", "S", "D", "M", "A", "B")) or first == ".MODEL"):
            raise ValueError(f"{adapter.kind}: expansion uses unsupported primitive")


def _adapter_from_json(data: Mapping[str, Any], source: str) -> ComponentAdapter:
    allowed = {"schema_version", "kind", "terminals", "parameters", "expansion", "description_zh", "description_en", "maturity"}
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"unknown adapter fields: {sorted(unknown)}")
    if data.get("schema_version") != 1:
        raise ValueError("adapter schema_version must be 1")
    raw_parameters = data.get("parameters", [])
    if not isinstance(raw_parameters, list):
        raise ValueError("adapter parameters must be a list")
    parameters_list: list[ParameterDefinition] = []
    for item in raw_parameters:
        if not isinstance(item, dict):
            raise ValueError("each adapter parameter must be an object")
        unknown_parameter_fields = set(item) - {
            "name", "default", "minimum", "maximum"
        }
        if unknown_parameter_fields:
            raise ValueError(
                "unknown adapter parameter fields: "
                f"{sorted(unknown_parameter_fields)}"
            )
        if "name" not in item or "default" not in item:
            raise ValueError("adapter parameters require name and default")
        parameters_list.append(
            ParameterDefinition(
                str(item["name"]).upper(),
                _number(item["default"], "parameter default"),
                (
                    None
                    if item.get("minimum") is None
                    else _number(item["minimum"], "parameter minimum")
                ),
                (
                    None
                    if item.get("maximum") is None
                    else _number(item["maximum"], "parameter maximum")
                ),
            )
        )
    raw_terminals = data.get("terminals", [])
    raw_expansion = data.get("expansion", [])
    if not isinstance(raw_terminals, list) or not all(
        isinstance(item, str) for item in raw_terminals
    ):
        raise ValueError("adapter terminals must be a list of strings")
    if not isinstance(raw_expansion, list) or not all(
        isinstance(item, str) for item in raw_expansion
    ):
        raise ValueError("adapter expansion must be a list of strings")
    adapter = ComponentAdapter(
        kind=str(data.get("kind", "")).upper(),
        terminals=tuple(raw_terminals),
        parameters=tuple(parameters_list),
        expansion=tuple(raw_expansion),
        description_zh=str(data.get("description_zh", "")),
        description_en=str(data.get("description_en", "")),
        maturity=str(data.get("maturity", "community")),
        source=source,
    )
    _validate_adapter(adapter)
    return adapter


def adapter_registry() -> dict[str, ComponentAdapter]:
    """Load built-ins plus strictly declarative local adapter packs."""
    registry = dict(BUILTIN_ADAPTERS)
    directory = os.environ.get("MULTISIM_MCP_ADAPTER_DIR", "").strip()
    if not directory:
        return registry
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"MULTISIM_MCP_ADAPTER_DIR is not a directory: {root}")
    for path in sorted(root.glob("*.json")):
        if path.is_symlink():
            raise ValueError(f"adapter pack must not be a symbolic link: {path.name}")
        try:
            path.resolve().relative_to(root)
        except ValueError as exc:
            raise ValueError(f"adapter pack escapes its configured directory: {path.name}") from exc
        if path.stat().st_size > _MAX_PACK_BYTES:
            raise ValueError(f"adapter pack is too large: {path.name}")
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data if isinstance(data, list) else [data]
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"adapter pack entries must be objects: {path.name}")
            adapter = _adapter_from_json(entry, path.name)
            if adapter.kind in registry:
                raise ValueError(f"adapter kind conflicts with an existing adapter: {adapter.kind}")
            registry[adapter.kind] = adapter
    return registry


def _format_number(number: float) -> str:
    return f"{number:.15g}"


def expand_component_adapters(text: str) -> str:
    """Expand ``Xname nodes... @KIND key=value`` pseudo-components."""
    registry = adapter_registry()
    rendered: list[str] = []
    invocation_count = 0
    next_ref: dict[str, int] = {}
    existing_counts: dict[str, int] = {}
    for source_line in text.splitlines():
        source_parts = source_line.strip().split()
        if not source_parts or source_line.lstrip().startswith(("*", ";", ".")):
            continue
        if any(part.startswith("@") for part in source_parts):
            continue
        prefix = source_parts[0][0].upper()
        if prefix.isalpha():
            existing_counts[prefix] = existing_counts.get(prefix, 0) + 1
        match = re.fullmatch(r"([A-Za-z])(\d+)", source_parts[0])
        if match:
            prefix, number = match.group(1).upper(), int(match.group(2))
            next_ref[prefix] = max(next_ref.get(prefix, 1), number + 1)
    for prefix, count in existing_counts.items():
        next_ref[prefix] = max(next_ref.get(prefix, 1), count + 1)
    used_stems: set[str] = set()

    def allocate(prefix: str) -> str:
        number = next_ref.get(prefix, 1)
        next_ref[prefix] = number + 1
        return f"{prefix}{number}"

    for raw in text.splitlines():
        stripped = raw.strip()
        parts = stripped.split()
        marker_index = next((i for i, part in enumerate(parts) if part.startswith("@")), -1)
        if not stripped or stripped.startswith(("*", ";")) or marker_index < 0:
            rendered.append(raw)
            continue
        if not parts[0].upper().startswith("X") or marker_index < 2:
            raise ValueError("adapter invocation must be Xname nodes... @KIND key=value")
        kind = parts[marker_index][1:].upper()
        invocation_count += 1
        if invocation_count > _MAX_INVOCATIONS:
            raise ValueError(f"component adapter invocation limit exceeds {_MAX_INVOCATIONS}")
        adapter = registry.get(kind)
        if adapter is None:
            raise ValueError(f"unknown component adapter: {kind}")
        nodes = parts[1:marker_index]
        if len(nodes) != len(adapter.terminals):
            raise ValueError(f"{parts[0]} @{kind} expects {len(adapter.terminals)} terminals, got {len(nodes)}")
        if any(not _NODE_RE.fullmatch(node) for node in nodes):
            raise ValueError(f"{parts[0]} contains an unsafe node name")
        raw_values: dict[str, str] = {}
        for token in parts[marker_index + 1:]:
            if "=" not in token:
                raise ValueError(f"{parts[0]} parameters must use KEY=value")
            key, value = token.split("=", 1)
            key = key.upper()
            if key in raw_values:
                raise ValueError(f"{parts[0]} repeats parameter {key}")
            raw_values[key] = value
        definitions = {item.name: item for item in adapter.parameters}
        unknown = set(raw_values) - set(definitions)
        if unknown:
            raise ValueError(f"{parts[0]} has unknown parameters: {sorted(unknown)}")
        values: dict[str, str] = {}
        numeric: dict[str, float] = {}
        for name, definition in definitions.items():
            number = _number(raw_values.get(name, definition.default), f"{parts[0]} {name}")
            if definition.minimum is not None and number < definition.minimum:
                raise ValueError(f"{parts[0]} {name} is below {definition.minimum}")
            if definition.maximum is not None and number > definition.maximum:
                raise ValueError(f"{parts[0]} {name} is above {definition.maximum}")
            numeric[name] = number
            values[name] = _format_number(number)
        if kind == "POTENTIOMETER":
            total, position, rmin = numeric["R"], numeric["POSITION"], numeric["RMIN"]
            values["RA"] = _format_number(max(total * position, rmin))
            values["RB"] = _format_number(max(total * (1 - position), rmin))
        # Multisim's native netlist exporter silently drops some components
        # whose generated reference designator contains punctuation.
        stem_base = re.sub(r"[^A-Za-z0-9]", "", parts[0][1:]) or "ADAPTER"
        stem = stem_base
        stem_suffix = 2
        while stem.casefold() in used_stems:
            stem = f"{stem_base}{stem_suffix}"
            stem_suffix += 1
        used_stems.add(stem.casefold())
        context = {"stem": stem, **dict(zip(adapter.terminals, nodes)), **values}
        expanded = [
            _PLACEHOLDER_RE.sub(lambda match: context[match.group(1)], line)
            for line in adapter.expansion
        ]
        # Multisim keeps conventional letter+number reference designators but
        # may silently renumber descriptive SPICE names such as RPOT_A.  Give
        # every generated primitive a collision-free conventional refdes, and
        # update cross-references such as coupled-inductor K lines.
        ref_mapping: dict[str, str] = {}
        for line in expanded:
            tokens = line.split()
            if not tokens or tokens[0].startswith("."):
                continue
            old_ref = tokens[0]
            prefix = old_ref[0].upper()
            ref_mapping[old_ref] = allocate(prefix)
        for line in expanded:
            tokens = line.split()
            rendered.append(" ".join(ref_mapping.get(token, token) for token in tokens))
    suffix = "\n" if text.endswith("\n") else ""
    result = "\n".join(rendered) + suffix
    if len(result.encode("utf-8")) > _MAX_EXPANDED_BYTES:
        raise ValueError("expanded component-adapter netlist exceeds 4 MB")
    return result


def component_adapter_catalog() -> dict[str, Any]:
    """Return the public adapter API catalog without proprietary templates."""
    items = []
    for adapter in adapter_registry().values():
        items.append(
            {
                "kind": adapter.kind,
                "syntax": f"Xname {' '.join(adapter.terminals)} @{adapter.kind} "
                + " ".join(f"{p.name}={_format_number(p.default)}" for p in adapter.parameters),
                "terminals": list(adapter.terminals),
                "parameters": [
                    {"name": p.name, "default": p.default, "minimum": p.minimum, "maximum": p.maximum}
                    for p in adapter.parameters
                ],
                "description_zh": adapter.description_zh,
                "description_en": adapter.description_en,
                "maturity": adapter.maturity,
                "source": adapter.source,
            }
        )
    return {"schema_version": 1, "adapter_directory": os.environ.get("MULTISIM_MCP_ADAPTER_DIR") or None, "adapters": items}


for _adapter in BUILTIN_ADAPTERS.values():
    _validate_adapter(_adapter)
