"""Execute the bounded DC network planner through the native analog runner."""
from __future__ import annotations
from typing import Any
from .natural_dc_network import parse_natural_dc_network
from .generated_analog_run import run_generated_analog_project

def _repair_dc_symbol(output: str, voltage: float) -> None:
    """Patch the encoded carrier's cached AC label in the editable .ms14."""
    from pathlib import Path
    from .multisim_client import Ms14Codec
    import re
    native = Path(output) / "native" / "circuit.ms14"
    if not native.is_file():
        return
    decoded = native.with_name(".dc-display.ms14.xml")
    patched = native.with_name(".dc-display.ms14")
    Ms14Codec().decode(str(native), str(decoded))
    text = decoded.read_text(encoding="utf-8")
    text = re.sub(r'(?s)Output="&amp;UNI10Vpk\s*5kHz\s*0_uc100b0\s*"', f'Output="&amp;ASCDC {voltage:g}V "', text)
    text = re.sub(r'(<CIITProbeExtComponent\b[^>]*\bHidden=")0("[^>]*>)', r'\g<1>1\g<2>', text)
    decoded.write_text(text, encoding="utf-8")
    Ms14Codec().encode(str(decoded), str(patched))
    native.write_bytes(patched.read_bytes())

def run_natural_dc_network(text: str, output: str, *, execute: bool = False) -> dict[str, Any]:
    plan = parse_natural_dc_network(text)
    if not execute:
        return {"success": True, "mode": "preview", "verification_status": "unverified", "natural_language_plan": plan}
    result = run_generated_analog_project(plan["proposal"], output, execute=True)
    _repair_dc_symbol(output, plan["derived"]["input_v"])
    return {"success": result.get("success", False), "mode": "execute", "verification_status": result.get("verification_status", "failed"), "natural_language_plan": plan, "selected_result": result, "report": result.get("report"), "output_dir": output}

__all__ = ["run_natural_dc_network"]
