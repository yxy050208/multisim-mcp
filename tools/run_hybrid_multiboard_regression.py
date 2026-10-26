"""Run the bounded mixed digital/analog multi-board regression."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp_server"))

from multisim_mcp.hybrid_multiboard_regression import (  # noqa: E402
    hybrid_multiboard_regression_cases,
    select_hybrid_multiboard_regression_case,
)
from multisim_mcp.native_multiboard_acceptance import run_native_multiboard_acceptance  # noqa: E402


def _write_archive_manifest(root: Path) -> None:
    files = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name != "archive-manifest.json":
            files[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root / "archive-manifest.json").write_text(
        json.dumps({"schema_version": 1, "kind": "hybrid-multiboard-archive", "files": files},
                   ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--case", default=None)
    parser.add_argument("--target-version", default="14.3")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--list", action="store_true",
        help="list all registered cases as JSON without building or starting COM",
    )
    args = parser.parse_args(argv)
    if args.list:
        print(json.dumps(
            [case.manifest() for case in hybrid_multiboard_regression_cases()],
            ensure_ascii=False, indent=2,
        ))
        return 0
    if args.output is None:
        parser.error("--output is required unless --list is used")
    case = select_hybrid_multiboard_regression_case(args.case)
    design, partition, fixtures = case.build()
    if args.target_version != case.target_version:
        raise SystemExit(
            f"case {case.case_id} is reviewed for {case.target_version}; "
            f"requested {args.target_version} remains unverified"
        )
    if not args.execute:
        print(json.dumps({"status": "logical-only", "case": case.manifest(),
                          "native_connector_status": "native-verified",
                          "verification_status": "unverified"}, ensure_ascii=False, indent=2))
        return 0
    from multisim_mcp import server
    result = run_native_multiboard_acceptance(
        design, partition, fixtures, args.output, execute=True,
        target_multisim_version=args.target_version, analysis=case.analysis,
        analysis_options=case.analysis_options,
        digital_edge_contracts=(case.digital_edge_contracts() if hasattr(case, "digital_edge_contracts") else None),
        converter_transfer_contract=(case.converter_transfer_contract() if hasattr(case, "converter_transfer_contract") else None),
        continuous_response_contracts=(case.continuous_response_contracts() if hasattr(case, "continuous_response_contracts") else None),
        schematic_executor=server._create_schematic_impl, client=server.client,
    )
    # The shared acceptance engine is intentionally case-agnostic. Persist
    # the selected regression identity next to its native evidence so offline
    # verification cannot accidentally apply the first (DNOT4) contract to a
    # different board pair.
    result["case_id"] = case.case_id
    acceptance_path = args.output / "acceptance.json"
    if acceptance_path.is_file():
        acceptance_payload = json.loads(acceptance_path.read_text(encoding="utf-8"))
        acceptance_payload["case_id"] = case.case_id
        acceptance_path.write_text(
            json.dumps(acceptance_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    if args.output.is_dir():
        _write_archive_manifest(args.output)
    print(json.dumps({
        "status": result.get("status"), "verification_status": result.get("verification_status"),
        "case_id": case.case_id, "output_directory": result.get("output_directory"),
        "native_acceptance": result.get("native_acceptance"), "error": result.get("error"),
    }, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "accepted" else 1


if __name__ == "__main__":
    raise SystemExit(main())
