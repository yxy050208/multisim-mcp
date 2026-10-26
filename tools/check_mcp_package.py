"""Verify that public MCP distributions contain only the expected code assets."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from pathlib import Path


TOOLS_ROOT = Path(__file__).resolve().parent
if str(TOOLS_ROOT) not in sys.path:
    sys.path.insert(0, str(TOOLS_ROOT))

from public_asset_policy import forbidden_public_asset_reason  # noqa: E402


def _verify_member_paths(names: list[str], label: str) -> None:
    for name in names:
        reason = forbidden_public_asset_reason(name)
        if reason:
            raise ValueError(f"{label} contains a forbidden public asset ({reason}): {name}")


def verify(dist: Path, version: str) -> tuple[Path, Path]:
    wheels = list(dist.glob(f"multisim_mcp-{version}-*.whl"))
    sdists = list(dist.glob(f"multisim_mcp-{version}.tar.gz"))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ValueError(
            f"expected one wheel and one sdist for {version}; "
            f"found {len(wheels)} wheel(s) and {len(sdists)} sdist(s)"
        )
    wheel, sdist = wheels[0], sdists[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        _verify_member_paths(names, "wheel")
        if sum(name.endswith("/SKILL.md") for name in names) != 5:
            raise ValueError("wheel must contain exactly five packaged Skills")
        if not any(name.endswith("licenses/LICENSE") for name in names):
            raise ValueError("wheel is missing its license")
        if not any(
            name == "multisim_mcp/native_xml.py"
            or name.endswith("/multisim_mcp/native_xml.py")
            for name in names
        ):
            raise ValueError("wheel is missing multisim_mcp/native_xml.py")
        if any("/templates/" in name and name.endswith(".xml") for name in names):
            raise ValueError("wheel contains a forbidden extracted XML template")

    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
        _verify_member_paths(names, "sdist")
        if sum(name.endswith("/SKILL.md") for name in names) != 5:
            raise ValueError("sdist must contain exactly five packaged Skills")
        if not any(name.endswith("/LICENSE") for name in names):
            raise ValueError("sdist is missing its license")
        if any("/templates/" in name and name.endswith(".xml") for name in names):
            raise ValueError("sdist contains a forbidden extracted XML template")
    return wheel, sdist


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    wheel, sdist = verify(args.dist, args.version)
    print(f"verified {wheel.name} and {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
