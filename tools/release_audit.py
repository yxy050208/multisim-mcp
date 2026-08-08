"""Fail closed when a GitHub release candidate contains local/proprietary assets.

中文输出为主；每项同时使用稳定的英文检查标识，便于 CI 和贡献者定位。
This script performs read-only checks and never changes Git state.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "mcp_server" / "pyproject.toml"


@dataclass
class Finding:
    level: str
    check: str
    message: str
    path: str | None = None


def run_git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def candidate_files() -> list[str]:
    result = run_git("ls-files", "--cached", "--others", "--exclude-standard")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git ls-files failed")
    return sorted(line for line in result.stdout.splitlines() if line)


def is_forbidden_asset(path: str) -> bool:
    item = PurePosixPath(path)
    lowered = path.lower()
    if item.parts and item.parts[0] == "analysis":
        return True
    if lowered.endswith((".ms14", ".ms14.xml", ".chm", ".dll", ".tlb", ".whl")):
        return True
    if (
        lowered.startswith("mcp_server/multisim_mcp/templates/")
        and lowered.endswith(".xml")
    ):
        return True
    return False


def audit() -> list[Finding]:
    findings: list[Finding] = []
    required = (
        "LICENSE",
        "README.md",
        "README.en.md",
        "CHANGELOG.md",
        "SECURITY.md",
        "THIRD_PARTY_NOTICES.md",
        "docs/PUBLISHING.md",
        "docs/RELEASE_NOTES_v0.1.0-alpha.md",
    )
    for relative in required:
        if not (ROOT / relative).is_file():
            findings.append(Finding("error", "required-doc", "缺少发布文档", relative))

    candidates = candidate_files()
    for relative in candidates:
        if is_forbidden_asset(relative):
            findings.append(
                Finding(
                    "error",
                    "forbidden-asset",
                    "公开候选中包含本地研究、二进制或许可未确认资产",
                    relative,
                )
            )

    # Avoid spelling a real user profile directly in the policy source itself.
    absolute_user_path = re.compile(r"[A-Za-z]:[\\/]Users[\\/][^\\/\s]+", re.I)
    secret_markers = re.compile(
        r"(?i)(github_pat_|ghp_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
    )
    text_suffixes = {".py", ".md", ".toml", ".json", ".yml", ".yaml", ".ps1", ".txt"}
    for relative in candidates:
        if relative == "tools/release_audit.py":
            continue
        path = ROOT / relative
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        if path.stat().st_size > 2_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if absolute_user_path.search(text):
            findings.append(
                Finding("error", "personal-path", "发现用户目录绝对路径", relative)
            )
        if secret_markers.search(text):
            findings.append(
                Finding("error", "secret-marker", "发现疑似令牌或私钥", relative)
            )

    if PYPROJECT.is_file():
        pyproject_text = PYPROJECT.read_text(encoding="utf-8")
        version_match = re.search(
            r'(?m)^version\s*=\s*"([^"]+)"\s*$', pyproject_text
        )
        version = version_match.group(1) if version_match else None
        if version != "0.1.0a1":
            findings.append(
                Finding("error", "version", "pyproject 版本应为 0.1.0a1", str(PYPROJECT.relative_to(ROOT)))
            )
        repository_match = re.search(
            r'(?m)^Repository\s*=\s*"([^"]+)"\s*$', pyproject_text
        )
        repository = repository_match.group(1) if repository_match else ""
        if not repository.startswith("https://github.com/") or "<" in repository:
            findings.append(
                Finding(
                    "error",
                    "repository-url",
                    "发布前必须填写真实 GitHub Repository URL",
                    str(PYPROJECT.relative_to(ROOT)),
                )
            )

    remotes = run_git("remote", "get-url", "origin")
    if remotes.returncode or not remotes.stdout.strip():
        findings.append(Finding("error", "git-origin", "尚未配置 Git origin"))

    manifest = ROOT / "mcp_server" / "multisim_mcp" / "templates" / "manifest.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        if payload.get("schema_version") != 1 or not payload.get("notice"):
            raise ValueError("schema_version/notice missing")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        findings.append(
            Finding("error", "template-manifest", f"模板 manifest 无效: {exc}", str(manifest.relative_to(ROOT)))
        )

    whitespace = run_git("diff", "--check")
    if whitespace.returncode:
        findings.append(
            Finding("error", "diff-check", whitespace.stdout.strip() or whitespace.stderr.strip())
        )

    status = run_git("status", "--porcelain")
    if status.stdout.strip():
        findings.append(
            Finding("warning", "dirty-tree", "工作区仍有未提交修改；最终打标签前必须再次审计")
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    try:
        findings = audit()
    except (OSError, RuntimeError) as exc:
        findings = [Finding("error", "audit-runtime", str(exc))]

    errors = [item for item in findings if item.level == "error"]
    warnings = [item for item in findings if item.level == "warning"]
    if args.json:
        print(
            json.dumps(
                {
                    "passed": not errors,
                    "errors": len(errors),
                    "warnings": len(warnings),
                    "findings": [asdict(item) for item in findings],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for item in findings:
            location = f" [{item.path}]" if item.path else ""
            print(f"{item.level.upper():7} {item.check}: {item.message}{location}")
        state = "PASS" if not errors else "FAIL"
        print(f"\n发布审计 / Release audit: {state} ({len(errors)} errors, {len(warnings)} warnings)")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
