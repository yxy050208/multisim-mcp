"""Verify that public MCP distributions contain only the expected code assets."""

from __future__ import annotations

import argparse
import tarfile
import zipfile
from pathlib import Path


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
        if sum(name.endswith("/SKILL.md") for name in names) != 5:
            raise ValueError("wheel must contain exactly five packaged Skills")
        if not any(name.endswith("licenses/LICENSE") for name in names):
            raise ValueError("wheel is missing its license")
        if any("/templates/" in name and name.endswith(".xml") for name in names):
            raise ValueError("wheel contains a forbidden extracted XML template")

    with tarfile.open(sdist, "r:gz") as archive:
        names = archive.getnames()
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
