"""Validate npm 11/12 ``npm pack --json`` output for the Harness bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPECTED_FILES = ["LICENSE", "README.md", "cordis.patch.yml", "package.json"]


def validate_pack_result(
    payload: Any,
    *,
    expected_name: str,
    expected_version: str,
) -> dict[str, Any]:
    if isinstance(payload, list):
        packages = payload
    elif isinstance(payload, dict):
        packages = list(payload.values())
    else:
        raise ValueError("npm pack JSON must be an array or object")
    if len(packages) != 1 or not isinstance(packages[0], dict):
        raise ValueError("npm pack must return exactly one package")

    package = packages[0]
    if package.get("name") != expected_name:
        raise ValueError(
            f"expected package {expected_name!r}, found {package.get('name')!r}"
        )
    if package.get("version") != expected_version:
        raise ValueError(
            f"expected version {expected_version!r}, found {package.get('version')!r}"
        )
    files = package.get("files")
    if not isinstance(files, list) or any(not isinstance(item, dict) for item in files):
        raise ValueError("npm pack files must be an array of objects")
    raw_paths = [item.get("path") for item in files]
    if any(not isinstance(path, str) for path in raw_paths):
        raise ValueError("every npm pack file must have a string path")
    paths = sorted(raw_paths)
    if paths != sorted(EXPECTED_FILES):
        raise ValueError(f"unexpected npm package files: {paths!r}")
    filename = package.get("filename")
    if not isinstance(filename, str) or not filename.endswith(".tgz"):
        raise ValueError("npm pack did not report a tarball filename")
    return {
        "id": package.get("id"),
        "name": package["name"],
        "version": package["version"],
        "filename": filename,
        "size": package.get("size"),
        "unpacked_size": package.get("unpackedSize"),
        "files": paths,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate npm pack JSON output")
    parser.add_argument("path", type=Path)
    parser.add_argument(
        "--expected-name", default="multisim-mcp-dsh-plugin"
    )
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--filename-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = json.loads(args.path.read_text(encoding="utf-8"))
    result = validate_pack_result(
        payload,
        expected_name=args.expected_name,
        expected_version=args.expected_version,
    )
    if args.filename_only:
        print(result["filename"])
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
