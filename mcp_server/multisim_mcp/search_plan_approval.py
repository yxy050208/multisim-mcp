"""Exact, one-time approval tokens for bounded search-plan submission.

The workbench only produces a read-only ``spec_draft``.  This module is the
separate approval boundary used by a future submitter: the bearer token is
bound to the optimization identity, the complete draft digest, and the
budget summary.  No raw token is persisted and issuing a token never starts
an experiment.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Mapping


SEARCH_PLAN_APPROVAL_SCHEMA_VERSION: Final = 2
SEARCH_PLAN_APPROVAL_STORE_ENV: Final = "MULTISIM_MCP_SEARCH_APPROVAL_STORE"
DEFAULT_SEARCH_PLAN_APPROVAL_TTL_SECONDS: Final = 900
MIN_SEARCH_PLAN_APPROVAL_TTL_SECONDS: Final = 60
MAX_SEARCH_PLAN_APPROVAL_TTL_SECONDS: Final = 86_400
MAX_SEARCH_PLAN_DOCUMENT_BYTES: Final = 8 * 1024 * 1024
MAX_SEARCH_PLAN_TOKEN_BYTES: Final = 512

_TOKEN_RE = re.compile(r"^mspsa_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")
_APPROVAL_ID_RE = re.compile(r"^search-approval-[0-9a-f]{32}$")
_CONSUMER_ID_RE = re.compile(r"^search-submit-[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be UTC")
    return parsed


def _strict_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate field in search-plan JSON: {key}")
        result[key] = value
    return result


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def search_plan_digest(value: object) -> str:
    """Return the stable SHA-256 digest used for exact draft binding."""
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _existing_file(path: str | os.PathLike[str], name: str) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = unresolved.resolve()
    if resolved == Path(resolved.anchor) or not resolved.is_file():
        raise FileNotFoundError(f"{name} must be an existing regular file")
    if resolved.stat().st_size > MAX_SEARCH_PLAN_DOCUMENT_BYTES:
        raise ValueError(f"{name} exceeds the 8 MiB size limit")
    return resolved


def _new_file(path: str | os.PathLike[str], name: str) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink() or unresolved.parent.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = unresolved.resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError(f"{name} must not be a filesystem root")
    if not resolved.parent.is_dir() or resolved.parent.is_symlink():
        raise FileNotFoundError(f"{name} parent must be an existing regular directory")
    if resolved.exists():
        raise FileExistsError(f"{name} already exists: {resolved}")
    return resolved


def read_search_plan_document(path: str | os.PathLike[str]) -> dict[str, Any]:
    source = _existing_file(path, "search-plan document")
    try:
        value = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda constant: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {constant}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError("search-plan document must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"search-plan document is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("search-plan document must contain one JSON object")
    return value


def write_search_plan_token(path: str | os.PathLike[str], token: str) -> dict[str, Any]:
    """Create a private token file without replacing an existing file."""
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("search-plan approval token is invalid")
    output = _new_file(path, "search-plan token output")
    temporary = output.with_name(f".{output.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write((token + "\n").encode("ascii"))
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.link(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(output),
        "created": True,
        "replace_existing": False,
        "user_only_permissions_requested": True,
    }


def read_search_plan_token(path: str | os.PathLike[str]) -> str:
    source = _existing_file(path, "search-plan token file")
    if source.stat().st_size > MAX_SEARCH_PLAN_TOKEN_BYTES:
        raise ValueError("search-plan token file exceeds the 512-byte size limit")
    try:
        token = source.read_text(encoding="ascii").strip()
    except UnicodeError as exc:
        raise ValueError("search-plan token file must be ASCII") from exc
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("search-plan token file is invalid")
    return token


def _default_store() -> Path:
    configured = os.environ.get(SEARCH_PLAN_APPROVAL_STORE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return (base / "multisim-mcp" / "search-approvals").resolve()
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return (base / "multisim-mcp" / "search-approvals").resolve()


def _fsync_directory(directory: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            with contextlib.suppress(OSError):
                os.close(descriptor)


def _atomic_create_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_replace_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(_json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_identifier(value: object, name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _validate_binding(binding: Mapping[str, Any]) -> None:
    fields = {
        "entry_handle",
        "optimization_id",
        "source_optimization_kind",
        "source_design_sha256",
        "source_spec_sha256",
        "spec_draft_sha256",
        "exploration_budget",
        "max_experiments",
    }
    if set(binding) != fields:
        raise ValueError("search-plan approval binding fields are invalid")
    _validate_identifier(binding.get("entry_handle"), "entry_handle")
    _validate_identifier(binding.get("optimization_id"), "optimization_id")
    if binding.get("source_optimization_kind") not in {
        "design-optimization",
        "global-optimization",
    }:
        raise ValueError("source_optimization_kind is invalid")
    for name in (
        "source_design_sha256",
        "source_spec_sha256",
        "spec_draft_sha256",
    ):
        value = binding.get(name)
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"{name} is invalid")
    for name in ("exploration_budget", "max_experiments"):
        value = binding.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} is invalid")
    if binding["max_experiments"] < 1 or binding["exploration_budget"] > binding["max_experiments"] - 1:
        raise ValueError("search-plan budget is invalid")


def build_search_plan_binding(
    *,
    entry_handle: str,
    optimization_id: str,
    source_optimization_kind: str,
    source_design: Mapping[str, Any],
    source_spec: Mapping[str, Any],
    spec_draft: Mapping[str, Any],
    exploration_budget: int,
    max_experiments: int,
) -> dict[str, Any]:
    """Validate a non-executable draft and produce its exact approval binding."""
    if not isinstance(source_design, Mapping) or not isinstance(source_spec, Mapping):
        raise ValueError("source_design and source_spec must be JSON objects")
    if not isinstance(spec_draft, Mapping):
        raise ValueError("spec_draft must be a JSON object")
    if spec_draft.get("available") is not True:
        raise ValueError("only an available search draft can be approved")
    if spec_draft.get("source_optimization_kind") != source_optimization_kind:
        raise ValueError("search draft source optimization kind does not match the binding")
    if spec_draft.get("read_only") is not True or spec_draft.get("executable") is not False:
        raise ValueError("search draft must remain read-only and non-executable")
    if spec_draft.get("review_required") is not True:
        raise ValueError("search draft must require review")
    preflight = spec_draft.get("preflight")
    if not isinstance(preflight, Mapping) or preflight.get("status") != "ready_for_review":
        raise ValueError("search draft preflight is not ready for review")
    if preflight.get("approval_required") is not True or preflight.get("execution_enabled") is not False:
        raise ValueError("search draft preflight has an unsafe approval state")
    parameters = spec_draft.get("parameters")
    if not isinstance(parameters, list) or not parameters:
        raise ValueError("search draft has no parameters")
    allocated = 0
    for item in parameters:
        if not isinstance(item, Mapping) or not isinstance(item.get("name"), str):
            raise ValueError("search draft parameter is invalid")
        values = item.get("values")
        share = item.get("budget_share")
        if not isinstance(values, list) or not 0 < len(values) <= 16:
            raise ValueError("search draft parameter values are not bounded")
        if isinstance(share, bool) or not isinstance(share, int) or share < 0:
            raise ValueError("search draft budget share is invalid")
        allocated += share
    if isinstance(exploration_budget, bool) or not isinstance(exploration_budget, int) or exploration_budget < 0:
        raise ValueError("exploration_budget is invalid")
    if isinstance(max_experiments, bool) or not isinstance(max_experiments, int) or max_experiments < 1:
        raise ValueError("max_experiments is invalid")
    if spec_draft.get("max_experiments") != max_experiments:
        raise ValueError("search draft max_experiments does not match the binding")
    if allocated > exploration_budget or max_experiments != exploration_budget + 1:
        raise ValueError("search draft budget summary does not match the binding")
    binding = {
        "entry_handle": entry_handle,
        "optimization_id": optimization_id,
        "source_optimization_kind": source_optimization_kind,
        "source_design_sha256": search_plan_digest(source_design),
        "source_spec_sha256": search_plan_digest(source_spec),
        "spec_draft_sha256": search_plan_digest(spec_draft),
        "exploration_budget": exploration_budget,
        "max_experiments": max_experiments,
    }
    _validate_binding(binding)
    return binding


def _validate_record(record: Mapping[str, Any]) -> None:
    allowed = {
        "schema_version",
        "kind",
        "approval_id",
        "status",
        "issued_at",
        "expires_at",
        "consumed_at",
        "consumer_id",
        "token_sha256",
        "entry_handle",
        "optimization_id",
        "source_optimization_kind",
        "source_design_sha256",
        "source_spec_sha256",
        "spec_draft_sha256",
        "exploration_budget",
        "max_experiments",
    }
    if set(record) != allowed:
        raise ValueError("search-plan approval record fields are invalid")
    if record.get("schema_version") != SEARCH_PLAN_APPROVAL_SCHEMA_VERSION:
        raise ValueError(
            "search-plan approval record schema_version must be "
            f"{SEARCH_PLAN_APPROVAL_SCHEMA_VERSION}"
        )
    if record.get("kind") != "multisim-mcp-search-plan-approval":
        raise ValueError("search-plan approval record kind is invalid")
    approval_id = record.get("approval_id")
    if not isinstance(approval_id, str) or _APPROVAL_ID_RE.fullmatch(approval_id) is None:
        raise ValueError("search-plan approval_id is invalid")
    if record.get("status") not in {"approved", "consumed"}:
        raise ValueError("search-plan approval status is invalid")
    _parse_timestamp(record.get("issued_at"), "issued_at")
    _parse_timestamp(record.get("expires_at"), "expires_at")
    for name in (
        "token_sha256",
        "source_design_sha256",
        "source_spec_sha256",
        "spec_draft_sha256",
    ):
        value = record.get(name)
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"search-plan approval {name} is invalid")
    _validate_binding({name: record.get(name) for name in (
        "entry_handle", "optimization_id", "source_optimization_kind",
        "source_design_sha256", "source_spec_sha256", "spec_draft_sha256",
        "exploration_budget", "max_experiments",
    )})
    issued = _parse_timestamp(record["issued_at"], "issued_at")
    expires = _parse_timestamp(record["expires_at"], "expires_at")
    if expires <= issued:
        raise ValueError("search-plan approval expiration is invalid")
    if record["status"] == "approved":
        if record.get("consumed_at") is not None or record.get("consumer_id") is not None:
            raise ValueError("approved search-plan record must not contain consumption data")
    else:
        _parse_timestamp(record.get("consumed_at"), "consumed_at")
        consumer = record.get("consumer_id")
        if not isinstance(consumer, str) or _CONSUMER_ID_RE.fullmatch(consumer) is None:
            raise ValueError("consumed search-plan consumer_id is invalid")


class SearchPlanApprovalClaim:
    def __init__(self, store: "SearchPlanApprovalStore", record: dict[str, Any], lock: Path):
        self.store = store
        self.record = record
        self.lock = lock
        self.consumed = False

    def consume(self, consumer_id: str) -> None:
        if self.consumed:
            raise RuntimeError("search-plan approval is already consumed")
        if _CONSUMER_ID_RE.fullmatch(consumer_id) is None:
            raise ValueError("consumer_id is invalid")
        updated = dict(self.record)
        updated.update(
            {
                "status": "consumed",
                "consumed_at": _timestamp(_utc_now()),
                "consumer_id": consumer_id,
            }
        )
        self.store._write_record(updated)
        self.record = updated
        self.consumed = True

    def __enter__(self) -> "SearchPlanApprovalClaim":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        with contextlib.suppress(OSError):
            self.lock.unlink(missing_ok=True)


class SearchPlanApprovalStore:
    """One-time search-plan approval records whose bearer secrets are never stored."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        unresolved = _default_store() if root is None else Path(root).expanduser()
        if unresolved.is_symlink():
            raise ValueError("search approval store must not be a symbolic link")
        self.root = unresolved.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("search approval store must not be a filesystem root")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("search approval store must be a regular directory")
        with contextlib.suppress(OSError):
            self.root.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def _record_path(self, approval_id: str) -> Path:
        if _APPROVAL_ID_RE.fullmatch(approval_id) is None:
            raise ValueError("search-plan approval_id is invalid")
        return self.root / f"{approval_id}.json"

    def _write_record(self, record: Mapping[str, Any]) -> None:
        approval_id = record.get("approval_id")
        if not isinstance(approval_id, str):
            raise ValueError("search-plan approval record has no approval_id")
        _atomic_replace_json(self._record_path(approval_id), record)

    def _read_record(self, approval_id: str) -> dict[str, Any]:
        path = self._record_path(approval_id)
        if path.is_symlink() or not path.is_file():
            raise FileNotFoundError("search-plan approval record is missing")
        if path.stat().st_size > MAX_SEARCH_PLAN_DOCUMENT_BYTES:
            raise ValueError("search-plan approval record exceeds the 8 MiB size limit")
        try:
            value = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_strict_object_pairs,
                parse_constant=lambda constant: (_ for _ in ()).throw(ValueError("non-finite JSON")),
            )
        except FileNotFoundError as exc:
            raise FileNotFoundError("search-plan approval record is missing") from exc
        except UnicodeError as exc:
            raise ValueError("search-plan approval record must be UTF-8") from exc
        except json.JSONDecodeError as exc:
            raise ValueError("search-plan approval record is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("search-plan approval record must contain an object")
        _validate_record(value)
        if value["approval_id"] != approval_id:
            raise ValueError("search-plan approval filename does not match its contents")
        return value

    def issue(
        self,
        binding: Mapping[str, Any],
        *,
        ttl_seconds: int = DEFAULT_SEARCH_PLAN_APPROVAL_TTL_SECONDS,
    ) -> dict[str, Any]:
        _validate_binding(binding)
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not MIN_SEARCH_PLAN_APPROVAL_TTL_SECONDS <= ttl_seconds <= MAX_SEARCH_PLAN_APPROVAL_TTL_SECONDS
        ):
            raise ValueError(
                f"ttl_seconds must be between {MIN_SEARCH_PLAN_APPROVAL_TTL_SECONDS} and "
                f"{MAX_SEARCH_PLAN_APPROVAL_TTL_SECONDS}"
            )
        approval_hex = uuid.uuid4().hex
        approval_id = f"search-approval-{approval_hex}"
        secret = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
        token = f"mspsa_{approval_hex}_{secret}"
        issued = _utc_now()
        record = {
            "schema_version": SEARCH_PLAN_APPROVAL_SCHEMA_VERSION,
            "kind": "multisim-mcp-search-plan-approval",
            "approval_id": approval_id,
            "status": "approved",
            "issued_at": _timestamp(issued),
            "expires_at": _timestamp(issued + timedelta(seconds=ttl_seconds)),
            "consumed_at": None,
            "consumer_id": None,
            "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
            **dict(binding),
        }
        _validate_record(record)
        _atomic_create_json(self._record_path(approval_id), record)
        return {
            "schema_version": SEARCH_PLAN_APPROVAL_SCHEMA_VERSION,
            "approval_id": approval_id,
            "approval_token": token,
            "expires_at": record["expires_at"],
            "optimization_id": binding["optimization_id"],
            "source_design_sha256": binding["source_design_sha256"],
            "source_spec_sha256": binding["source_spec_sha256"],
            "spec_draft_sha256": binding["spec_draft_sha256"],
            "exploration_budget": binding["exploration_budget"],
            "max_experiments": binding["max_experiments"],
            "one_time": True,
            "token_persisted": False,
            "execution_started": False,
        }

    def _validate_token(self, token: str, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if not isinstance(token, str):
            raise ValueError("search-plan approval token must be a string")
        match = _TOKEN_RE.fullmatch(token)
        if match is None:
            raise ValueError("search-plan approval token is invalid")
        record = self._read_record(f"search-approval-{match.group(1)}")
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        if not hmac.compare_digest(record["token_sha256"], digest):
            raise ValueError("search-plan approval token is invalid")
        if record["status"] != "approved":
            raise ValueError("search-plan approval token has already been consumed")
        if _utc_now() >= _parse_timestamp(record["expires_at"], "expires_at"):
            raise ValueError("search-plan approval token has expired")
        if expected is not None:
            _validate_binding(expected)
            for key, value in expected.items():
                if record.get(key) != value:
                    raise ValueError(f"search-plan approval does not match current {key}")
        return record

    def inspect(self, token: str, expected: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = self._validate_token(token, expected)
        return {
            "schema_version": SEARCH_PLAN_APPROVAL_SCHEMA_VERSION,
            "approval_id": record["approval_id"],
            "status": record["status"],
            "expires_at": record["expires_at"],
            "optimization_id": record["optimization_id"],
            "source_design_sha256": record["source_design_sha256"],
            "source_spec_sha256": record["source_spec_sha256"],
            "spec_draft_sha256": record["spec_draft_sha256"],
            "exploration_budget": record["exploration_budget"],
            "max_experiments": record["max_experiments"],
            "one_time": True,
            "execution_started": False,
        }

    def claim(self, token: str, expected: Mapping[str, Any]) -> SearchPlanApprovalClaim:
        self._validate_token(token, expected)
        match = _TOKEN_RE.fullmatch(token)
        assert match is not None
        approval_id = f"search-approval-{match.group(1)}"
        record_path = self._record_path(approval_id)
        lock = self.root / f".{approval_id}.lock"
        try:
            with lock.open("xb") as handle:
                handle.write(f"pid={os.getpid()}\n".encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RuntimeError("search-plan approval is already being used") from exc
        try:
            current = self._validate_token(token, expected)
            return SearchPlanApprovalClaim(self, current, lock)
        except Exception:
            lock.unlink(missing_ok=True)
            raise


__all__ = [
    "DEFAULT_SEARCH_PLAN_APPROVAL_TTL_SECONDS",
    "MAX_SEARCH_PLAN_APPROVAL_TTL_SECONDS",
    "MIN_SEARCH_PLAN_APPROVAL_TTL_SECONDS",
    "SEARCH_PLAN_APPROVAL_SCHEMA_VERSION",
    "SearchPlanApprovalClaim",
    "SearchPlanApprovalStore",
    "build_search_plan_binding",
    "read_search_plan_document",
    "read_search_plan_token",
    "search_plan_digest",
    "write_search_plan_token",
]
