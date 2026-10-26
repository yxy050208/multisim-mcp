"""Public, explicit ideal-model contracts shared by synthesis and verification."""

from __future__ import annotations

import hashlib
import json
from typing import Any


IDEAL_OPAMP5_GAIN_SPICE = "1e5"
OPEN_LOOP_GAIN = float(IDEAL_OPAMP5_GAIN_SPICE)
IDEAL_OPAMP5_SOURCE = "builtin:opamp5-vcvs-v1"
IDEAL_OPAMP5_PIN_ORDER = ("IN+", "IN-", "VS+", "VS-", "OUT")
IDEAL_OPAMP5_LIMITATIONS = (
    "Supply pins are connected but do not limit the ideal output voltage.",
    "No rail clipping, finite bandwidth, slew rate, output-current limit, noise or vendor behavior.",
    "No physical part number, device ratings or hardware suitability is implied.",
)


def ideal_opamp5_contract() -> dict[str, Any]:
    """Return fresh metadata for the native carrier's existing VCVS behavior."""
    return {
        "model": "OPAMP5",
        "source": IDEAL_OPAMP5_SOURCE,
        "license": "MIT",
        "pin_order": list(IDEAL_OPAMP5_PIN_ORDER),
        "open_loop_gain_v_per_v": OPEN_LOOP_GAIN,
        "native_spice_template": f"e%p %tOUT 0 %tIN+ %tIN- {IDEAL_OPAMP5_GAIN_SPICE}",
        "limitations": list(IDEAL_OPAMP5_LIMITATIONS),
    }


IDEAL_OPAMP5_SHA256 = hashlib.sha256(
    json.dumps(ideal_opamp5_contract(), sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
