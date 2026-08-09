"""Extract reusable native component templates from decoded Multisim XML.

This is a reverse-engineering aid for locally licensed Multisim installations.
It extracts the selected component, its schematic symbol, its ports, and any
matching virtual-instrument state; the caller is responsible for provenance
and redistribution review.
"""

from __future__ import annotations

import argparse
import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def _asc(value: str) -> str:
    return value if value.startswith("&ASC") else f"&ASC{value}"


def _port_sort_key(item: ET.Element) -> tuple[int, str]:
    port = item.find("./CiPort")
    name = (port.get("LocalName") or "").removeprefix("&ASC")
    match = re.search(r"\d+", name)
    return (int(match.group()) if match else 1_000_000, name)


def _write_template(path: Path, element: ET.Element) -> Path:
    """Write decoder-compatible fragments without formatting text nodes.

    The Multisim XML codec preserves whitespace-only nodes. Pretty-printing an
    extracted component therefore changes the encoded object graph and can make
    Multisim silently omit an otherwise valid part from its native netlist.
    """
    for node in element.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    ET.ElementTree(element).write(path, encoding="ASCII", xml_declaration=True)
    return path


def extract_templates(
    source: Path,
    refdes: str,
    kind: str,
    output_dir: Path,
) -> list[Path]:
    root = ET.parse(source).getroot()
    component_item: ET.Element | None = None
    component: ET.Element | None = None
    for item in root.iter("Item"):
        candidate = item.find("./CiComponent")
        if candidate is not None and candidate.get("LocalName") == _asc(refdes):
            component_item = item
            component = candidate
            break
    if component_item is None or component is None:
        raise ValueError(f"Component {refdes!r} was not found in {source}")

    component_id = component_item.get("CiID")
    symbol_item: ET.Element | None = None
    for item in root.iter("Item"):
        symbol = item.find("./CIITSymbolComp")
        if symbol is not None and symbol.get("CiComponent") == component_id:
            symbol_item = item
            break
    if symbol_item is None:
        raise ValueError(f"No CIITSymbolComp was linked to {refdes!r}")

    port_items: list[ET.Element] = []
    for item in root.iter("Item"):
        port = item.find("./CiPort")
        if port is not None and port.get("Component") == component_id:
            port_items.append(item)
    port_items.sort(key=_port_sort_key)
    if not port_items:
        raise ValueError(f"No ports were linked to {refdes!r}")

    output_dir.mkdir(parents=True, exist_ok=True)
    kind = kind.lower()
    outputs: list[tuple[Path, ET.Element]] = [
        (output_dir / f"{kind}_element.xml", copy.deepcopy(component_item)),
        (output_dir / f"sym_{kind}.xml", copy.deepcopy(symbol_item)),
    ]
    outputs.extend(
        (
            output_dir
            / (
                f"{kind}_port.xml"
                if kind == "gnd" and len(port_items) == 1
                else f"{kind}_port{index}.xml"
            ),
            copy.deepcopy(item),
        )
        for index, item in enumerate(port_items, start=1)
    )

    # Virtual instruments (for example XSC1) keep their front-panel settings
    # outside the component tree. Preserve that state as a separate template;
    # the circuit builder rewrites CompLongName for each generated instance.
    instrument_marker = f"{refdes.upper()}#"
    for node in root.iter("CSourceSymbolCollectNode"):
        long_name = (node.get("CompLongName") or "").upper()
        if instrument_marker in long_name:
            outputs.append(
                (
                    output_dir / f"{kind}_instrument.xml",
                    copy.deepcopy(node),
                )
            )
            break

    # The builder supplies placement through the symbol root transform. Only
    # extract source instances that already use the standard root transform.
    symbol = outputs[1][1].find("./CIITSymbolComp")
    if symbol is None or symbol.get("Transformer-M20") is None:
        raise ValueError(
            f"{refdes!r} is not a standard-orientation symbol; choose another source instance"
        )
    symbol.set("Transformer-M00", "1.")
    symbol.set("Transformer-M01", "0.")
    symbol.set("Transformer-M10", "0.")
    symbol.set("Transformer-M11", "1.")
    symbol.set("Transformer-M20", "0.")
    symbol.set("Transformer-M21", "0.")

    written: list[Path] = []
    for path, element in outputs:
        written.append(_write_template(path, element))
    return written


def extract_structural_templates(
    source: Path,
    output_dir: Path,
    *,
    minimal_source: Path | None = None,
) -> list[Path]:
    """Derive reusable wiring plus a blank version-matched project shell."""
    root = ET.parse(source).getroot()
    output_dir.mkdir(parents=True, exist_ok=True)

    def first_item(class_name: str, predicate) -> ET.Element:
        for item in root.iter("Item"):
            if item.get("Class") == class_name and predicate(item):
                return copy.deepcopy(item)
        raise ValueError(f"No structural {class_name} template matched in {source}")

    node_zero = first_item(
        "CiNode",
        lambda item: (item.find("./CiNode") is not None)
        and item.find("./CiNode").get("LocalName") == "&ASC0",
    )
    node_named = first_item(
        "CiNode",
        lambda item: (item.find("./CiNode") is not None)
        and item.find("./CiNode").get("LocalName") not in {None, "", "&ASC0"},
    )
    node_text = first_item(
        "CODNodeTextComp", lambda item: item.find("./CODNodeTextComp") is not None
    )

    def wire_with_modifier(expected: str):
        def predicate(item: ET.Element) -> bool:
            wire = item.find("./CIITLinkComp")
            modifier = (
                wire.find("./ElectricalObject/ModifierInfo/Element")
                if wire is not None
                else None
            )
            return modifier is not None and modifier.get("NetModifier") == expected

        return predicate

    wire = first_item("CIITLinkComp", wire_with_modifier("&ASCNI_EWB_NET_AUTONAMED"))
    wire_named = first_item("CIITLinkComp", wire_with_modifier("&ASCNI_EWB_NET_NAME"))

    def pin_with(mobility: str, connection_class: str, minimum: int = 1):
        def predicate(item: ET.Element) -> bool:
            pin = item.find("./CODPinComp")
            if pin is None or pin.get("Mobility") != mobility:
                return False
            connections = pin.findall("./ConnectList/Item")
            return len(connections) >= minimum and all(
                child.get("Class") == connection_class for child in connections
            )

        return predicate

    extpin = first_item("CODPinComp", pin_with("1", "CIITPinConnectorComp"))
    junction_member = first_item("CODPinComp", pin_with("1", "CODPinComp"))
    junction_owner = first_item("CODPinComp", pin_with("0", "CODPinComp", minimum=2))

    minimal_path = minimal_source or source
    minimal = ET.parse(minimal_path).getroot()
    main_diagram = None
    circuit_item = None
    for diagram in minimal.iter("CIITDiagram"):
        elements = diagram.find("./Elements")
        composite = diagram.find("./Components/CODComposite")
        if elements is None or composite is None:
            continue
        candidate = next(
            (child for child in elements if child.get("Class") == "CiCircuit"),
            None,
        )
        if candidate is not None:
            main_diagram, circuit_item = diagram, candidate
            break
    if main_diagram is None or circuit_item is None:
        raise ValueError(f"No main circuit diagram was found in {minimal_path}")
    composite = main_diagram.find("./Components/CODComposite")
    elements = main_diagram.find("./Elements")
    for container_name in ("Objects", "ReferencedComponents"):
        container = composite.find(f"./{container_name}")
        if container is not None:
            container.clear()
    for child in list(elements):
        if child is not circuit_item:
            elements.remove(child)
    circuit = circuit_item.find("./CiCircuit")
    for container_name in ("Nodes", "Components", "ProbeExts"):
        container = circuit.find(f"./{container_name}") if circuit is not None else None
        if container is not None:
            container.clear()
    for tag in ("InstrumentsData", "CIRToInfoMap", "TriggerSet"):
        for container in minimal.iter(tag):
            container.clear()

    outputs = (
        ("minimal.ms14.xml", minimal),
        ("node_v0.xml", node_zero),
        ("node_named.xml", node_named),
        ("nodetext.xml", node_text),
        ("wire.xml", wire),
        ("wire_named.xml", wire_named),
        ("extpin.xml", extpin),
        ("junction_member.xml", junction_member),
        ("junction_owner.xml", junction_owner),
    )
    return [
        _write_template(output_dir / name, element) for name, element in outputs
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Decoded .ms14.xml file")
    parser.add_argument("refdes", help="Component reference designator, for example C1")
    parser.add_argument("kind", help="Template kind/prefix, for example C")
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    for path in extract_templates(args.source, args.refdes, args.kind, args.output_dir):
        print(path)


if __name__ == "__main__":
    main()
