"""Overlay a verified native component onto a user-local template pack.

This tool never publishes the extracted template.  It decodes a circuit from
the user's licensed Multisim installation, verifies an identity token, backs
up any replaced local templates, and records source/file hashes in the local
pack manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from multisim_mcp.multisim_client import Ms14Codec

from extract_native_component_templates import extract_templates


_KIND_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_REFDES_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_pack_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if root == Path(root.anchor):
        raise ValueError("pack must not be a filesystem root")
    manifest = root / "local-pack-manifest.json"
    if not root.is_dir() or not manifest.is_file():
        raise ValueError("pack must contain local-pack-manifest.json")
    return root


def _read_manifest(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read local pack manifest: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("local_only") is not True:
        raise ValueError("overlay target must be a user-local component pack")
    if int(payload.get("schema_version", 0)) != 2:
        raise ValueError("overlay target uses an unsupported manifest schema")
    return payload


def overlay_component(
    pack: Path,
    source: Path,
    refdes: str,
    kind: str,
    identity_token: str,
    *,
    force: bool = False,
    codec: Ms14Codec | None = None,
) -> dict:
    root = _safe_pack_root(pack)
    source = source.expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".ms14":
        raise ValueError("source must be an existing .ms14 circuit")
    normalized_refdes = refdes.strip()
    normalized_kind = kind.strip().upper()
    token = identity_token.strip()
    if not _REFDES_PATTERN.fullmatch(normalized_refdes):
        raise ValueError("refdes has an invalid format")
    if not _KIND_PATTERN.fullmatch(normalized_kind):
        raise ValueError("kind has an invalid format")
    if not token or len(token) > 128 or any(ord(char) < 32 for char in token):
        raise ValueError("identity_token must contain 1-128 printable characters")

    manifest_path = root / "local-pack-manifest.json"
    manifest = _read_manifest(manifest_path)
    decoder = codec or Ms14Codec()
    with tempfile.TemporaryDirectory(prefix="multisim-mcp-overlay-") as temp:
        stage = Path(temp)
        source_copy = stage / source.name
        shutil.copy2(source, source_copy)
        decoded = Path(decoder.decode(str(source_copy))["xml"])
        payload = decoded.read_bytes().replace(b"\x00", b"")
        decoded.write_bytes(payload)
        written = extract_templates(decoded, normalized_refdes, normalized_kind, stage)
        combined = b"\n".join(path.read_bytes() for path in written).decode(
            "utf-8", errors="ignore"
        )
        if token.casefold() not in combined.casefold():
            raise ValueError(
                f"identity token {token!r} was not found in extracted templates"
            )

        existing = [root / path.name for path in written if (root / path.name).exists()]
        if existing and not force:
            raise FileExistsError(
                "overlay would replace existing local templates; pass --force: "
                + ", ".join(path.name for path in existing)
            )
        backup_files: list[str] = []
        if existing:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = root / "overlay-backups" / f"{stamp}-{normalized_kind.lower()}"
            backup.mkdir(parents=True, exist_ok=False)
            for path in existing:
                target = backup / path.name
                shutil.copy2(path, target)
                backup_files.append(str(target.relative_to(root)).replace("\\", "/"))

        installed: list[dict[str, object]] = []
        for staged in written:
            destination = root / staged.name
            shutil.copy2(staged, destination)
            installed.append(
                {
                    "path": destination.name,
                    "size": destination.stat().st_size,
                    "sha256": _sha256(destination),
                }
            )

    overlay = {
        "kind": normalized_kind,
        "refdes": normalized_refdes,
        "identity_token": token,
        "source_filename": source.name,
        "source_sha256": _sha256(source),
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "files": installed,
        "backup_files": backup_files,
        "local_only": True,
        "redistribution_review_required": True,
    }
    overlays = manifest.setdefault("overlays", [])
    if not isinstance(overlays, list):
        raise ValueError("local pack overlays field must be a list")
    overlays.append(overlay)
    known_files = manifest.setdefault("files", [])
    if isinstance(known_files, list):
        manifest["files"] = sorted(
            {str(item) for item in known_files}
            | {str(item["path"]) for item in installed}
        )
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(manifest_path)
    return {
        "pack": str(root),
        "manifest": str(manifest_path),
        "overlay": overlay,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--refdes", required=True)
    parser.add_argument("--kind", required=True)
    parser.add_argument("--identity-token", required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    result = overlay_component(
        args.pack,
        args.source,
        args.refdes,
        args.kind,
        args.identity_token,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
