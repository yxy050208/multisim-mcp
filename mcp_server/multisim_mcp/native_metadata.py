"""Read bounded component identity evidence from locally decoded Multisim XML.

The extractor intentionally returns hashes instead of model/template bodies so
snapshots can prove local identity without redistributing licensed content.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Final


NATIVE_METADATA_SCHEMA_VERSION: Final = 1
_MAX_XML_BYTES: Final = 128 * 1024 * 1024
_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _decode(value: str | None) -> str:
    return (value or "").removeprefix("&ASC").strip()


def _at(values: list[str], index: int) -> str:
    return values[index] if index < len(values) else ""


def _sha256(value: str) -> str | None:
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else None


def _external_refdes_map(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in root.iter("CIRToInfoMapItem"):
        internal = _decode(item.get("CIRKey"))
        info = item.find("./RefDesInfo")
        if not internal or info is None:
            continue
        prefix = _decode(info.get("IRPrefix"))
        number = str(info.get("IRNumber") or "").strip()
        section = _decode(info.get("IRSection"))
        external = f"{prefix}{number}{section}"
        if _REFDES_RE.fullmatch(external):
            result[internal.casefold()] = external
    return result


def extract_native_component_metadata(
    xml_path: str, *, expected_refdes: set[str] | None = None
) -> dict[str, Any]:
    """Extract component identity, hashes, and declared ports from decoded XML."""
    if not isinstance(xml_path, str) or not xml_path.strip():
        raise ValueError("xml_path must not be empty")
    path = Path(xml_path).expanduser()
    if path.is_symlink():
        raise ValueError("decoded XML path must not be a symbolic link")
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"decoded XML does not exist: {path}")
    if path.stat().st_size > _MAX_XML_BYTES:
        raise ValueError("decoded XML exceeds the 128 MiB safety limit")
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError("decoded Multisim XML is invalid") from exc

    refdes_map = _external_refdes_map(root)
    expected = {item.casefold() for item in expected_refdes or set()}
    ports: dict[str, str] = {}
    for item in root.iter("Item"):
        port = item.find("./CiPort")
        if port is not None and item.get("CiID"):
            ports[str(item.get("CiID"))] = _decode(port.get("LocalName"))

    components: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in root.iter("Item"):
        component = item.find("./CiComponent")
        if component is None:
            continue
        internal = _decode(component.get("LocalName"))
        external = refdes_map.get(internal.casefold(), internal)
        key = external.casefold()
        if not _REFDES_RE.fullmatch(external) or key in seen:
            continue
        if expected and key not in expected:
            continue
        collections: list[list[str]] = []
        for values in component.findall("./Attributes/Item/CiaCollString/strings"):
            collections.append([_decode(entry.get("Value")) for entry in values.findall("./Item")])
        primary = collections[0] if collections else []
        secondary = collections[1] if len(collections) > 1 else []
        model_name = _at(primary, 3) or _at(secondary, 3)
        model_material = _at(primary, 5) or _at(secondary, 5)
        spice_template = component.find("./Attributes/Item/CiaSpiceTmpltExprt")
        template_text = _decode(spice_template.get("String")) if spice_template is not None else ""
        port_names = [
            ports.get(str(port.get("CiID")), "")
            for port in component.findall("./Ports/Item")
        ]
        port_names = [name for name in port_names if name]
        metadata = {
            "schema_version": NATIVE_METADATA_SCHEMA_VERSION,
            "refdes": external,
            "internal_refdes_sha256": _sha256(internal),
            "component_type": _at(primary, 0) or _at(secondary, 2),
            "database_name": _at(primary, 1),
            "model_name": model_name,
            "family": _at(primary, 14),
            "group": _at(primary, 15),
            "manufacturer": _at(primary, 16) or _at(secondary, 9),
            "description": _at(primary, 20),
            "port_names": port_names,
            "port_count": len(port_names),
            "model_definition_sha256": _sha256(model_material),
            "spice_template_sha256": _sha256(template_text),
            "model_verified": bool(model_name and (model_material or template_text)),
            "raw_model_material_included": False,
        }
        components.append(metadata)
        seen.add(key)
    return {
        "schema_version": NATIVE_METADATA_SCHEMA_VERSION,
        "kind": "multisim-mcp-native-component-metadata",
        "state": "verified" if components else "no-matching-components",
        "component_count": len(components),
        "components": components,
        "source_xml_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "raw_model_material_included": False,
    }


__all__ = ["NATIVE_METADATA_SCHEMA_VERSION", "extract_native_component_metadata"]
