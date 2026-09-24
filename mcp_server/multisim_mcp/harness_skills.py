"""Versioned DeepSeek Harness skill bundle and safe installer."""

from __future__ import annotations

import json
import os
import re
import uuid
from importlib.resources import files
from pathlib import Path
from typing import Any


_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _bundle_root() -> Any:
    return files("multisim_mcp").joinpath("harness_skills")


def bundled_harness_skill_manifest() -> dict[str, Any]:
    """Load and validate the packaged Harness skill manifest."""
    root = _bundle_root()
    payload = json.loads(root.joinpath("manifest.json").read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported Harness skill manifest schema")
    names = payload.get("skills")
    if not isinstance(names, list) or not names:
        raise ValueError("Harness skill manifest must contain a non-empty skills list")
    if len(set(names)) != len(names):
        raise ValueError("Harness skill manifest contains duplicate names")
    for name in names:
        if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid Harness skill name: {name!r}")
        content = root.joinpath(name, "SKILL.md").read_text(encoding="utf-8")
        if not content.startswith("---\n") or f"\nname: {name}\n" not in content:
            raise ValueError(f"Harness skill frontmatter does not match: {name}")
    return payload


def bundled_harness_skills() -> dict[str, str]:
    """Return the validated immutable skill bodies keyed by skill name."""
    manifest = bundled_harness_skill_manifest()
    root = _bundle_root()
    return {
        name: root.joinpath(name, "SKILL.md").read_text(encoding="utf-8")
        for name in manifest["skills"]
    }


def install_harness_skills(
    output_dir: str = ".dsh/skills", force: bool = False
) -> dict[str, Any]:
    """Install the bundle without silently replacing existing project skills."""
    if not output_dir.strip():
        raise ValueError("Harness skill output directory must not be empty")
    requested_root = Path(output_dir).expanduser()
    if requested_root.is_symlink():
        raise ValueError("Harness skill output must not be a symbolic link")
    root = requested_root.resolve()
    if root == Path(root.anchor):
        raise ValueError("Harness skill output directory must not be a filesystem root")
    if root.exists() and not root.is_dir():
        raise ValueError("Harness skill output must be a regular directory")

    manifest = bundled_harness_skill_manifest()
    skills = bundled_harness_skills()
    destinations = {name: root / name / "SKILL.md" for name in skills}
    for destination in destinations.values():
        parent = destination.parent
        if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
            raise ValueError(
                f"Harness skill directory must be a regular directory: {parent}"
            )
        if parent.exists():
            try:
                parent.resolve().relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    "Harness skill destination escapes the output root"
                ) from exc
        if destination.is_symlink() or (
            destination.exists() and not destination.is_file()
        ):
            raise ValueError(
                f"Harness skill destination must be a regular file: {destination}"
            )
        if destination.exists() and not force:
            raise FileExistsError(
                "Harness skill already exists; pass --force to replace the bundle: "
                f"{destination}"
            )

    installed: list[str] = []
    for name, content in skills.items():
        destination = destinations[name]
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = destination.parent.resolve()
        try:
            resolved_parent.relative_to(root)
        except ValueError as exc:
            raise ValueError("Harness skill destination escapes the output root") from exc
        temporary = destination.parent / f".SKILL.md.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        installed.append(str(destination))

    return {
        "schema_version": 1,
        "command": "harness-skills",
        "success": True,
        "bundle_name": manifest["bundle_name"],
        "bundle_version": manifest["bundle_version"],
        "output_dir": str(root),
        "force": force,
        "skill_count": len(installed),
        "skills": list(skills),
        "installed": installed,
    }
