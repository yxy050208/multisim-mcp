"""Build a user-local Multisim component template pack from licensed samples.

This tool does not grant redistribution rights. It is intended to let each user
derive interoperability templates from their own Multisim installation instead
of downloading extracted NI assets from this project.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from multisim_mcp.multisim_client import Ms14Codec

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_native_component_templates import (
    extract_structural_templates,
    extract_templates,
)


@dataclass(frozen=True)
class Extraction:
    sample: str
    refdes: str
    kind: str


EXTRACTIONS = (
    Extraction("LowPassFilter.ms14", "R11", "R"),
    Extraction("LowPassFilter.ms14", "C9", "C"),
    Extraction("LowPassFilter.ms14", "V1", "V"),
    Extraction("LowPassFilter.ms14", "0", "GND"),
    Extraction("Analyses/Monte Carlo - RLC Circuit.ms14", "L1", "L"),
    Extraction(
        "Analyses/Batched Analyses - CMOS Common Source Amplifier.ms14",
        "Iref",
        "I",
    ),
    Extraction("Analog/ClassBPushPullAmp.ms14", "D1", "D"),
    Extraction("Analog/ClassBPushPullAmp.ms14", "Q1", "QNPN"),
    Extraction("Analog/ClassBPushPullAmp.ms14", "Q2", "QPNP"),
    Extraction("Analyses/DC Sweep - CMOS Inverter.ms14", "Q1", "MNMOS"),
    Extraction("Analyses/DC Sweep - CMOS Inverter.ms14", "Q2", "MPMOS"),
    Extraction("LowPassFilter.ms14", "U4", "OPAMP5"),
    Extraction("Up-DownCounter.ms14", "U13", "DNOT4"),
    Extraction("Up-DownCounter.ms14", "U5", "DAND5"),
    Extraction("Up-DownCounter.ms14", "U14", "DOR5"),
    Extraction("Up-DownCounter.ms14", "U1", "DJK7"),
    Extraction("Getting Started/Getting Started 1.ms14", "R2", "XSUB16"),
    Extraction("LowPassFilter.ms14", "XSC1", "OSC6"),
    Extraction("Non-InvertingOpAmp.ms14", "XFG1", "XFG3"),
)


def build_pack(samples_root: Path, output: Path, force: bool) -> dict:
    samples_root = samples_root.expanduser().resolve()
    output = output.expanduser().resolve()
    missing = [item.sample for item in EXTRACTIONS if not (samples_root / item.sample).is_file()]
    if missing:
        raise FileNotFoundError(
            "Required licensed Multisim samples were not found: " + ", ".join(sorted(set(missing)))
        )
    if not force and output.exists():
        existing = sorted(
            [*output.glob("*.xml"), *output.glob("*manifest.json")]
        )
        if existing:
            raise FileExistsError(
                "Refusing to overwrite existing pack files: "
                + ", ".join(path.name for path in existing)
            )
    output.mkdir(parents=True, exist_ok=True)

    codec = Ms14Codec()
    written: list[str] = []
    decoded_by_sample: dict[str, Path] = {}
    with tempfile.TemporaryDirectory(prefix="multisim-mcp-pack-") as temp:
        temp_root = Path(temp)
        for item in EXTRACTIONS:
            decoded = decoded_by_sample.get(item.sample)
            if decoded is None:
                source = samples_root / item.sample
                local_copy = temp_root / source.name
                # Same filenames in different folders are disambiguated by a stable index.
                if local_copy.exists():
                    local_copy = temp_root / f"{len(decoded_by_sample)}-{source.name}"
                shutil.copy2(source, local_copy)
                decoded = Path(codec.decode(str(local_copy))["xml"])
                payload = decoded.read_bytes()
                if b"\x00" in payload:
                    # The third-party decoder occasionally emits a trailing NUL
                    # for otherwise valid XML. Remove only that invalid byte.
                    decoded.write_bytes(payload.replace(b"\x00", b""))
                decoded_by_sample[item.sample] = decoded
            for path in extract_templates(decoded, item.refdes, item.kind, output):
                written.append(path.name)
        for path in extract_structural_templates(
            decoded_by_sample["LowPassFilter.ms14"], output
        ):
            written.append(path.name)

    manifest = {
        "schema_version": 1,
        "local_only": True,
        "notice": (
            "Generated from the user's licensed Multisim samples. Do not redistribute "
            "without confirming NI and third-party model terms."
        ),
        "families": [
            {"kind": item.kind, "sample": item.sample, "refdes": item.refdes}
            for item in EXTRACTIONS
        ],
        "structural_source": "LowPassFilter.ms14",
        "files": sorted(set(written)),
    }
    manifest_path = output / "local-pack-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output), "files": len(set(written)), "manifest": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--samples-root",
        type=Path,
        default=(
            Path(
                os.environ.get(
                    "PUBLIC",
                    str(Path(os.environ.get("SystemDrive", "C:")) / "Users" / "Public"),
                )
            )
            / "Documents"
            / "National Instruments"
            / "Circuit Design Suite 14.3"
            / "samples"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build_pack(args.samples_root, args.output, args.force), indent=2))


if __name__ == "__main__":
    main()
