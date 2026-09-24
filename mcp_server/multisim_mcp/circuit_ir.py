"""Small, serializable circuit intermediate representation (CIR)."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any

@dataclass(frozen=True)
class IrComponent:
    ref: str
    kind: str
    value: str | None = None
    model: str | None = None
    pins: tuple[str, ...] = ()

@dataclass(frozen=True)
class IrConnection:
    net: str
    endpoint: str

@dataclass
class CircuitIR:
    components: list[IrComponent] = field(default_factory=list)
    connections: list[IrConnection] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)

    def validate(self) -> list[str]:
        errors: list[str] = []
        refs = [c.ref for c in self.components]
        if len(refs) != len(set(refs)):
            errors.append("duplicate component reference")
        known = {c.ref for c in self.components}
        for c in self.connections:
            ref = c.endpoint.split(".", 1)[0]
            if ref not in known and ref not in {"0", "GND"}:
                errors.append(f"unknown endpoint component: {c.endpoint}")
        nets: dict[str, int] = {}
        for c in self.connections:
            nets[c.net] = nets.get(c.net, 0) + 1
        errors.extend(f"net has fewer than two endpoints: {n}" for n, count in nets.items() if count < 2)
        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_spice(self) -> str:
        errors = self.validate()
        if errors:
            raise ValueError("invalid CIR: " + "; ".join(errors))
        nets: dict[str, list[str]] = {}
        for item in self.connections:
            nets.setdefault(item.net, []).append(item.endpoint)
        lines: list[str] = []
        for c in self.components:
            endpoints = []
            for net, items in nets.items():
                for endpoint in items:
                    if endpoint.startswith(c.ref + "."):
                        endpoints.append((endpoint.split(".", 1)[1], net))
            endpoints.sort(key=lambda item: (item[0].isdigit(), item[0]))
            nodes = [net for _, net in endpoints]
            if c.kind in {"R", "C", "L"} and len(nodes) == 2:
                lines.append(f"{c.ref} {nodes[0]} {nodes[1]} {c.value or '1k'}")
            elif c.kind == "V" and len(nodes) == 2:
                lines.append(f"{c.ref} {nodes[0]} {nodes[1]} DC {c.value or '0'}")
            else:
                raise ValueError(f"unsupported CIR component: {c.ref}/{c.kind}")
        return "\n".join(lines) + "\n.end\n"

__all__ = ["IrComponent", "IrConnection", "CircuitIR"]
