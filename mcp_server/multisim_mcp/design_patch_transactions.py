"""Approval-gated, atomic persistence for validated DesignPatch candidates."""

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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final, Mapping

from .design_patch_service import PreparedDesignPatch, prepare_design_patch
from .eda_core import CircuitDesign, DesignPatch


PATCH_APPROVAL_SCHEMA_VERSION: Final = 1
PATCH_TRANSACTION_SCHEMA_VERSION: Final = 1
PATCH_JOURNAL_SCHEMA_VERSION: Final = 1
PATCH_APPROVAL_STORE_ENV: Final = "MULTISIM_MCP_PATCH_APPROVAL_STORE"
DEFAULT_APPROVAL_TTL_SECONDS: Final = 900
MIN_APPROVAL_TTL_SECONDS: Final = 60
MAX_APPROVAL_TTL_SECONDS: Final = 86_400
MAX_PATCH_DOCUMENT_BYTES: Final = 8 * 1024 * 1024
MAX_APPROVAL_TOKEN_BYTES: Final = 512

_TOKEN_RE = re.compile(r"^mspat_([0-9a-f]{32})_([A-Za-z0-9_-]{43})$")
_APPROVAL_ID_RE = re.compile(r"^approval-[0-9a-f]{32}$")
_TRANSACTION_ID_RE = re.compile(r"^patch-txn-[0-9a-f]{32}$")
_JOURNAL_ID_RE = re.compile(r"^patch-journal-[0-9a-f]{32}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_JOURNAL_STATUSES = (
    "preparing",
    "prepared",
    "target_published",
    "receipt_published",
    "approval_consumed",
)


def _patch_crash_point(stage: str) -> None:
    """Test seam for process-crash state coverage; production is a no-op."""
    del stage


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
            raise ValueError(f"duplicate field in JSON document: {key}")
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


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory durability; unsupported Windows handles are ignored."""
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


def _path_fingerprint(path: Path) -> str:
    normalized = os.path.normcase(str(path.resolve()))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _existing_file(path: str | os.PathLike[str], name: str) -> Path:
    unresolved = Path(path).expanduser()
    if unresolved.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = unresolved.resolve()
    if resolved == Path(resolved.anchor) or not resolved.is_file():
        raise FileNotFoundError(f"{name} must be an existing regular file")
    if resolved.stat().st_size > MAX_PATCH_DOCUMENT_BYTES:
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


def _read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant: {value}")
            ),
        )
    except UnicodeError as exc:
        raise ValueError(f"{name} must be UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain one JSON object")
    return value


def read_design_document(path: str | os.PathLike[str]) -> tuple[Path, CircuitDesign]:
    source = _existing_file(path, "design file")
    return source, CircuitDesign.from_dict(_read_json(source, "design file"))


def read_patch_document(path: str | os.PathLike[str]) -> tuple[Path, DesignPatch]:
    source = _existing_file(path, "patch file")
    return source, DesignPatch.from_dict(_read_json(source, "patch file"))


def _default_approval_store() -> Path:
    configured = os.environ.get(PATCH_APPROVAL_STORE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        return (base / "multisim-mcp" / "patch-approvals").resolve()
    state = os.environ.get("XDG_STATE_HOME", "").strip()
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return (base / "multisim-mcp" / "patch-approvals").resolve()


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


def write_approval_token(
    path: str | os.PathLike[str], token: str
) -> dict[str, Any]:
    """Create a user-only token file without ever replacing an existing file."""
    if not isinstance(token, str) or _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("approval token is invalid")
    output = _new_file(path, "approval token output")
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


def read_approval_token(path: str | os.PathLike[str]) -> str:
    """Read a bounded token file and validate its complete contents."""
    source = _existing_file(path, "approval token file")
    if source.stat().st_size > MAX_APPROVAL_TOKEN_BYTES:
        raise ValueError("approval token file exceeds the 512-byte size limit")
    try:
        token = source.read_text(encoding="ascii").strip()
    except UnicodeError as exc:
        raise ValueError("approval token file must be ASCII") from exc
    if _TOKEN_RE.fullmatch(token) is None:
        raise ValueError("approval token file is invalid")
    return token


def _target_path(design_path: Path, output_path: str | None, in_place: bool) -> Path:
    if not isinstance(in_place, bool):
        raise ValueError("in_place must be a boolean")
    if in_place == (output_path is not None):
        raise ValueError("select exactly one of in_place or output_path")
    if in_place:
        return design_path
    assert output_path is not None
    target = _new_file(output_path, "design output")
    if target == design_path:
        raise ValueError("use in_place to replace the input design")
    return target


def _receipt_path(path: str | os.PathLike[str], *others: Path) -> Path:
    receipt = _new_file(path, "transaction receipt")
    if receipt in others:
        raise ValueError("transaction receipt must be a distinct new file")
    return receipt


def _prepared_for_persistence(
    design: CircuitDesign,
    patch: DesignPatch,
    regenerate_source_netlist: bool,
) -> PreparedDesignPatch:
    prepared = prepare_design_patch(
        design,
        patch,
        regenerate_source_netlist=regenerate_source_netlist,
    )
    if prepared.source_netlist_update_required:
        raise ValueError(
            "patch would stale the authoritative source netlist; explicitly "
            "authorize source regeneration"
        )
    return prepared


def _approval_expectation(
    *,
    operation: str,
    design_path: Path,
    design: CircuitDesign,
    patch: DesignPatch,
    prepared: PreparedDesignPatch,
    target: Path,
    receipt: Path,
    regenerate_source_netlist: bool,
    source_transaction_digest: str | None,
    authorization_context_digest: str | None,
) -> dict[str, Any]:
    if authorization_context_digest is not None and (
        not isinstance(authorization_context_digest, str)
        or _SHA256_RE.fullmatch(authorization_context_digest) is None
    ):
        raise ValueError("authorization_context_digest must be a SHA-256 digest")
    return {
        "operation": operation,
        "design_id": design.design_id,
        "base_revision": design.revision,
        "design_digest": _digest(design.to_dict()),
        "patch_id": patch.patch_id,
        "patch_digest": _digest(patch.to_dict()),
        "candidate_digest": _digest(prepared.candidate.to_dict()),
        "design_path_sha256": _path_fingerprint(design_path),
        "target_path_sha256": _path_fingerprint(target),
        "receipt_path_sha256": _path_fingerprint(receipt),
        "in_place": target == design_path,
        "regenerate_source_netlist": regenerate_source_netlist,
        "source_transaction_digest": source_transaction_digest,
        "authorization_context_digest": authorization_context_digest,
    }


class PatchApprovalClaim:
    def __init__(self, store: "PatchApprovalStore", record: dict[str, Any], lock: Path):
        self.store = store
        self.record = record
        self.lock = lock
        self.consumed = False

    def consume(self, transaction_id: str) -> None:
        if self.consumed:
            raise RuntimeError("approval is already consumed")
        if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise ValueError("transaction_id is invalid")
        updated = dict(self.record)
        updated["status"] = "consumed"
        updated["consumed_at"] = _timestamp(_utc_now())
        updated["transaction_id"] = transaction_id
        self.store._write_record(updated)
        self.record = updated
        self.consumed = True

    def __enter__(self) -> "PatchApprovalClaim":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        with contextlib.suppress(OSError):
            self.lock.unlink(missing_ok=True)


class PatchApprovalStore:
    """One-time approval records whose bearer secrets are never stored."""

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        unresolved = _default_approval_store() if root is None else Path(root).expanduser()
        if unresolved.is_symlink():
            raise ValueError("approval store must not be a symbolic link")
        self.root = unresolved.resolve()
        if self.root == Path(self.root.anchor):
            raise ValueError("approval store must not be a filesystem root")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError("approval store must be a regular directory")
        with contextlib.suppress(OSError):
            self.root.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

    def _record_path(self, approval_id: str) -> Path:
        if not _APPROVAL_ID_RE.fullmatch(approval_id):
            raise ValueError("approval_id is invalid")
        return self.root / f"{approval_id}.json"

    def _write_record(self, record: Mapping[str, Any]) -> None:
        approval_id = record.get("approval_id")
        if not isinstance(approval_id, str):
            raise ValueError("approval record has no approval_id")
        _atomic_replace_json(self._record_path(approval_id), record)

    def _read_record(self, approval_id: str) -> dict[str, Any]:
        path = self._record_path(approval_id)
        record = _read_json(_existing_file(path, "approval record"), "approval record")
        _validate_approval_record(record)
        if record["approval_id"] != approval_id:
            raise ValueError("approval record filename does not match its contents")
        return record

    def _consume_recovered(
        self,
        approval_id: str,
        transaction_id: str,
        expectation: Mapping[str, Any],
    ) -> bool:
        """Consume an already-started journal after exact on-disk validation."""
        if set(expectation) != _EXPECTATION_FIELDS:
            raise ValueError("recovery approval expectation fields are invalid")
        if not _TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise ValueError("recovery transaction_id is invalid")
        record = self._read_record(approval_id)
        for key, value in expectation.items():
            if record.get(key) != value:
                raise ValueError(f"recovery approval does not match journal {key}")
        if record["status"] == "consumed":
            if record["transaction_id"] != transaction_id:
                raise ValueError("approval was consumed by another transaction")
            return False
        updated = dict(record)
        updated["status"] = "consumed"
        updated["consumed_at"] = _timestamp(_utc_now())
        updated["transaction_id"] = transaction_id
        self._write_record(updated)
        return True

    def create(
        self,
        expectation: Mapping[str, Any],
        *,
        ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    ) -> dict[str, Any]:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not MIN_APPROVAL_TTL_SECONDS <= ttl_seconds <= MAX_APPROVAL_TTL_SECONDS
        ):
            raise ValueError(
                f"ttl_seconds must be between {MIN_APPROVAL_TTL_SECONDS} and "
                f"{MAX_APPROVAL_TTL_SECONDS}"
            )
        if set(expectation) != _EXPECTATION_FIELDS:
            raise ValueError("approval expectation fields are invalid")
        approval_hex = uuid.uuid4().hex
        approval_id = f"approval-{approval_hex}"
        secret = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")
        token = f"mspat_{approval_hex}_{secret}"
        issued = _utc_now()
        record = {
            "schema_version": PATCH_APPROVAL_SCHEMA_VERSION,
            "kind": "multisim-mcp-patch-approval",
            "approval_id": approval_id,
            "status": "approved",
            "issued_at": _timestamp(issued),
            "expires_at": _timestamp(issued + timedelta(seconds=ttl_seconds)),
            "consumed_at": None,
            "transaction_id": None,
            "token_sha256": hashlib.sha256(token.encode("ascii")).hexdigest(),
            **dict(expectation),
        }
        _validate_approval_record(record)
        _atomic_create_json(self._record_path(approval_id), record)
        return {
            "schema_version": PATCH_APPROVAL_SCHEMA_VERSION,
            "approval_id": approval_id,
            "approval_token": token,
            "expires_at": record["expires_at"],
            "operation": record["operation"],
            "design_id": record["design_id"],
            "patch_id": record["patch_id"],
            "one_time": True,
            "token_persisted": False,
        }

    def claim(
        self, token: str, expectation: Mapping[str, Any]
    ) -> PatchApprovalClaim:
        if not isinstance(token, str):
            raise ValueError("approval token must be a string")
        match = _TOKEN_RE.fullmatch(token)
        if match is None:
            raise ValueError("approval token is invalid")
        approval_id = f"approval-{match.group(1)}"
        record_path = self._record_path(approval_id)
        record = _read_json(_existing_file(record_path, "approval record"), "approval record")
        _validate_approval_record(record)
        if not hmac.compare_digest(
            record["token_sha256"], hashlib.sha256(token.encode("ascii")).hexdigest()
        ):
            raise ValueError("approval token is invalid")
        if record["status"] != "approved":
            raise ValueError("approval token has already been consumed")
        if _utc_now() >= _parse_timestamp(record["expires_at"], "expires_at"):
            raise ValueError("approval token has expired")
        for key, value in expectation.items():
            if record.get(key) != value:
                raise ValueError(f"approval does not match current {key}")
        lock = self.root / f".{approval_id}.lock"
        lock_created = False
        try:
            with lock.open("xb") as handle:
                lock_created = True
                handle.write(f"pid={os.getpid()}\n".encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RuntimeError("approval is already being used") from exc
        except Exception:
            if lock_created:
                with contextlib.suppress(OSError):
                    lock.unlink(missing_ok=True)
            raise
        try:
            current = _read_json(record_path, "approval record")
            _validate_approval_record(current)
            if not hmac.compare_digest(
                current["token_sha256"],
                hashlib.sha256(token.encode("ascii")).hexdigest(),
            ):
                raise ValueError("approval token is invalid")
            if current["status"] != "approved":
                raise ValueError("approval token has already been consumed")
            if _utc_now() >= _parse_timestamp(current["expires_at"], "expires_at"):
                raise ValueError("approval token has expired")
            for key, value in expectation.items():
                if current.get(key) != value:
                    raise ValueError(f"approval does not match current {key}")
            return PatchApprovalClaim(self, current, lock)
        except Exception:
            lock.unlink(missing_ok=True)
            raise


class _TargetLock:
    """Serialize writes made through this module to one resolved target path."""

    def __init__(self, target: Path) -> None:
        self.path = target.with_name(f".{target.name}.multisim-patch.lock")

    def __enter__(self) -> "_TargetLock":
        created = False
        try:
            with self.path.open("xb") as handle:
                created = True
                handle.write(f"pid={os.getpid()}\n".encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RuntimeError(
                f"design target is already locked: {self.path}"
            ) from exc
        except Exception:
            if created:
                with contextlib.suppress(OSError):
                    self.path.unlink(missing_ok=True)
            raise
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)


_EXPECTATION_FIELDS = {
    "operation",
    "design_id",
    "base_revision",
    "design_digest",
    "patch_id",
    "patch_digest",
    "candidate_digest",
    "design_path_sha256",
    "target_path_sha256",
    "receipt_path_sha256",
    "in_place",
    "regenerate_source_netlist",
    "source_transaction_digest",
    "authorization_context_digest",
}


def _validate_approval_record(record: Mapping[str, Any]) -> None:
    allowed = {
        "schema_version",
        "kind",
        "approval_id",
        "status",
        "issued_at",
        "expires_at",
        "consumed_at",
        "transaction_id",
        "token_sha256",
        *_EXPECTATION_FIELDS,
    }
    if set(record) != allowed:
        raise ValueError("approval record fields are invalid")
    if record.get("schema_version") != PATCH_APPROVAL_SCHEMA_VERSION:
        raise ValueError("approval record schema_version must be 1")
    if record.get("kind") != "multisim-mcp-patch-approval":
        raise ValueError("approval record kind is invalid")
    if not isinstance(record.get("approval_id"), str) or not _APPROVAL_ID_RE.fullmatch(record["approval_id"]):
        raise ValueError("approval record approval_id is invalid")
    if record.get("status") not in {"approved", "consumed"}:
        raise ValueError("approval record status is invalid")
    _parse_timestamp(record.get("issued_at"), "issued_at")
    _parse_timestamp(record.get("expires_at"), "expires_at")
    for key in (
        "token_sha256",
        "design_digest",
        "patch_digest",
        "candidate_digest",
        "design_path_sha256",
        "target_path_sha256",
        "receipt_path_sha256",
    ):
        if not isinstance(record.get(key), str) or not _SHA256_RE.fullmatch(record[key]):
            raise ValueError(f"approval record {key} is invalid")
    if record.get("operation") not in {"apply", "revert"}:
        raise ValueError("approval record operation is invalid")
    for key in ("in_place", "regenerate_source_netlist"):
        if not isinstance(record.get(key), bool):
            raise ValueError(f"approval record {key} must be a boolean")
    if (
        isinstance(record.get("base_revision"), bool)
        or not isinstance(record.get("base_revision"), int)
        or record["base_revision"] < 0
    ):
        raise ValueError("approval record base_revision is invalid")
    for key in ("design_id", "patch_id"):
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError(f"approval record {key} is invalid")
    source_digest = record.get("source_transaction_digest")
    if source_digest is not None and (
        not isinstance(source_digest, str) or not _SHA256_RE.fullmatch(source_digest)
    ):
        raise ValueError("approval record source_transaction_digest is invalid")
    context_digest = record.get("authorization_context_digest")
    if context_digest is not None and (
        not isinstance(context_digest, str)
        or not _SHA256_RE.fullmatch(context_digest)
    ):
        raise ValueError("approval record authorization_context_digest is invalid")
    issued = _parse_timestamp(record.get("issued_at"), "issued_at")
    expires = _parse_timestamp(record.get("expires_at"), "expires_at")
    if expires <= issued:
        raise ValueError("approval record expiration is invalid")
    if record["status"] == "approved":
        if record.get("consumed_at") is not None or record.get("transaction_id") is not None:
            raise ValueError("approved record must not contain consumption data")
    else:
        _parse_timestamp(record.get("consumed_at"), "consumed_at")
        transaction_id = record.get("transaction_id")
        if not isinstance(transaction_id, str) or not _TRANSACTION_ID_RE.fullmatch(transaction_id):
            raise ValueError("consumed approval transaction_id is invalid")


def _write_durable_file(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _copy_durable_file(source: Path, destination: Path) -> None:
    temporary = destination.with_name(
        f".{destination.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with source.open("rb") as reader, temporary.open("xb") as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
        os.link(temporary, destination)
        _fsync_directory(destination.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _design_digest_at(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        _, design = read_design_document(str(path))
    except (OSError, ValueError):
        return "unknown"
    return _digest(design.to_dict())


def _receipt_digest_at(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        value = _validate_transaction_receipt(_read_json(path, "transaction receipt"))
    except (OSError, ValueError):
        return "unknown"
    return _digest(value)


def _journal_path_for(target: Path, journal_id: str) -> Path:
    return target.with_name(
        f".{target.name}.{journal_id}.multisim-patch-journal.json"
    )


def _validate_patch_journal(
    value: Mapping[str, Any], *, source: Path | None = None
) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "kind",
        "journal_id",
        "transaction_id",
        "approval_id",
        "operation",
        "status",
        "created_at",
        "updated_at",
        "owner_pid",
        "design_source_path",
        "target_path",
        "receipt_path",
        "approval_store_path",
        "candidate_stage_path",
        "receipt_stage_path",
        "backup_path",
        "in_place",
        "input_design_digest",
        "output_design_digest",
        "receipt_digest",
        "approval_expectation",
    }
    if set(value) != allowed:
        raise ValueError("patch journal fields are invalid")
    if value.get("schema_version") != PATCH_JOURNAL_SCHEMA_VERSION:
        raise ValueError("patch journal schema_version must be 1")
    if value.get("kind") != "multisim-mcp-design-patch-journal":
        raise ValueError("patch journal kind is invalid")
    journal_id = value.get("journal_id")
    if not isinstance(journal_id, str) or not _JOURNAL_ID_RE.fullmatch(journal_id):
        raise ValueError("patch journal journal_id is invalid")
    transaction_id = value.get("transaction_id")
    if not isinstance(transaction_id, str) or not _TRANSACTION_ID_RE.fullmatch(
        transaction_id
    ):
        raise ValueError("patch journal transaction_id is invalid")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str) or not _APPROVAL_ID_RE.fullmatch(approval_id):
        raise ValueError("patch journal approval_id is invalid")
    if value.get("operation") not in {"apply", "revert"}:
        raise ValueError("patch journal operation is invalid")
    if value.get("status") not in _JOURNAL_STATUSES:
        raise ValueError("patch journal status is invalid")
    _parse_timestamp(value.get("created_at"), "journal created_at")
    _parse_timestamp(value.get("updated_at"), "journal updated_at")
    owner_pid = value.get("owner_pid")
    if isinstance(owner_pid, bool) or not isinstance(owner_pid, int) or owner_pid <= 0:
        raise ValueError("patch journal owner_pid is invalid")
    if not isinstance(value.get("in_place"), bool):
        raise ValueError("patch journal in_place must be a boolean")
    for key in ("input_design_digest", "output_design_digest", "receipt_digest"):
        digest = value.get(key)
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise ValueError(f"patch journal {key} is invalid")
    expectation = value.get("approval_expectation")
    if not isinstance(expectation, Mapping) or set(expectation) != _EXPECTATION_FIELDS:
        raise ValueError("patch journal approval expectation is invalid")
    for key in (
        "design_source_path",
        "target_path",
        "receipt_path",
        "approval_store_path",
        "candidate_stage_path",
        "receipt_stage_path",
    ):
        raw = value.get(key)
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"patch journal {key} is invalid")
    design_source = Path(value["design_source_path"]).resolve()
    target = Path(value["target_path"]).resolve()
    receipt = Path(value["receipt_path"]).resolve()
    approval_store = Path(value["approval_store_path"]).resolve()
    candidate_stage = Path(value["candidate_stage_path"]).resolve()
    receipt_stage = Path(value["receipt_stage_path"]).resolve()
    backup_raw = value.get("backup_path")
    if backup_raw is not None and not isinstance(backup_raw, str):
        raise ValueError("patch journal backup_path is invalid")
    backup = Path(backup_raw).resolve() if isinstance(backup_raw, str) else None
    if value["in_place"] != (design_source == target):
        raise ValueError("patch journal in-place path relationship is invalid")
    if value["in_place"] != (backup is not None):
        raise ValueError("patch journal backup relationship is invalid")
    expected_candidate = target.with_name(f".{target.name}.{journal_id}.candidate")
    expected_receipt = receipt.with_name(f".{receipt.name}.{journal_id}.receipt")
    expected_backup = target.with_name(f".{target.name}.{journal_id}.backup")
    if candidate_stage != expected_candidate or receipt_stage != expected_receipt:
        raise ValueError("patch journal staging paths are invalid")
    if backup is not None and backup != expected_backup:
        raise ValueError("patch journal backup path is invalid")
    if approval_store == Path(approval_store.anchor):
        raise ValueError("patch journal approval store is invalid")
    if source is not None and source.resolve() != _journal_path_for(target, journal_id):
        raise ValueError("patch journal filename does not match its contents")
    if expectation.get("operation") != value["operation"]:
        raise ValueError("patch journal operation does not match approval")
    if expectation.get("design_digest") != value["input_design_digest"]:
        raise ValueError("patch journal input digest does not match approval")
    if expectation.get("candidate_digest") != value["output_design_digest"]:
        raise ValueError("patch journal output digest does not match approval")
    if expectation.get("design_path_sha256") != _path_fingerprint(design_source):
        raise ValueError("patch journal design path does not match approval")
    if expectation.get("target_path_sha256") != _path_fingerprint(target):
        raise ValueError("patch journal target path does not match approval")
    if expectation.get("receipt_path_sha256") != _path_fingerprint(receipt):
        raise ValueError("patch journal receipt path does not match approval")
    return dict(value)


@dataclass(slots=True)
class _DurablePatchJournal:
    path: Path
    value: dict[str, Any]

    @classmethod
    def create(
        cls,
        *,
        design_source: Path,
        target: Path,
        receipt: Path,
        approval_store: Path,
        approval_id: str,
        operation: str,
        expectation: Mapping[str, Any],
        receipt_value: Mapping[str, Any],
    ) -> "_DurablePatchJournal":
        journal_id = f"patch-journal-{uuid.uuid4().hex}"
        path = _journal_path_for(target, journal_id)
        now = _timestamp(_utc_now())
        value = {
            "schema_version": PATCH_JOURNAL_SCHEMA_VERSION,
            "kind": "multisim-mcp-design-patch-journal",
            "journal_id": journal_id,
            "transaction_id": receipt_value["transaction_id"],
            "approval_id": approval_id,
            "operation": operation,
            "status": "preparing",
            "created_at": now,
            "updated_at": now,
            "owner_pid": os.getpid(),
            "design_source_path": str(design_source),
            "target_path": str(target),
            "receipt_path": str(receipt),
            "approval_store_path": str(approval_store),
            "candidate_stage_path": str(
                target.with_name(f".{target.name}.{journal_id}.candidate")
            ),
            "receipt_stage_path": str(
                receipt.with_name(f".{receipt.name}.{journal_id}.receipt")
            ),
            "backup_path": (
                str(target.with_name(f".{target.name}.{journal_id}.backup"))
                if target == design_source
                else None
            ),
            "in_place": target == design_source,
            "input_design_digest": expectation["design_digest"],
            "output_design_digest": expectation["candidate_digest"],
            "receipt_digest": _digest(receipt_value),
            "approval_expectation": dict(expectation),
        }
        normalized = _validate_patch_journal(value, source=path)
        staging_paths = [
            Path(normalized["candidate_stage_path"]),
            Path(normalized["receipt_stage_path"]),
        ]
        if normalized["backup_path"] is not None:
            staging_paths.append(Path(normalized["backup_path"]))
        for staging_path in staging_paths:
            _new_file(staging_path, "patch journal staging file")
        _atomic_create_json(path, normalized)
        journal = cls(path, normalized)
        _patch_crash_point("journal_created")
        return journal

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> "_DurablePatchJournal":
        source = _existing_file(path, "patch journal")
        value = _validate_patch_journal(
            _read_json(source, "patch journal"), source=source
        )
        return cls(source, value)

    @property
    def target(self) -> Path:
        return Path(self.value["target_path"])

    @property
    def receipt(self) -> Path:
        return Path(self.value["receipt_path"])

    @property
    def candidate_stage(self) -> Path:
        return Path(self.value["candidate_stage_path"])

    @property
    def receipt_stage(self) -> Path:
        return Path(self.value["receipt_stage_path"])

    @property
    def backup(self) -> Path | None:
        value = self.value["backup_path"]
        return Path(value) if isinstance(value, str) else None

    def _set_status(self, status: str) -> None:
        if status not in _JOURNAL_STATUSES:
            raise ValueError("patch journal status is invalid")
        updated = dict(self.value)
        updated["status"] = status
        updated["updated_at"] = _timestamp(_utc_now())
        updated["owner_pid"] = os.getpid()
        normalized = _validate_patch_journal(updated, source=self.path)
        _atomic_replace_json(self.path, normalized)
        self.value = normalized

    def prepare(self, design_bytes: bytes, receipt_bytes: bytes) -> None:
        _write_durable_file(self.candidate_stage, design_bytes)
        _write_durable_file(self.receipt_stage, receipt_bytes)
        if _design_digest_at(self.candidate_stage) != self.value["output_design_digest"]:
            raise ValueError("staged candidate digest mismatch")
        if _receipt_digest_at(self.receipt_stage) != self.value["receipt_digest"]:
            raise ValueError("staged receipt digest mismatch")
        if self.backup is not None:
            if _design_digest_at(self.target) != self.value["input_design_digest"]:
                raise ValueError("design changed before transaction backup")
            _copy_durable_file(self.target, self.backup)
            if _design_digest_at(self.backup) != self.value["input_design_digest"]:
                raise ValueError("transaction backup digest mismatch")
        self._set_status("prepared")
        _patch_crash_point("prepared")

    def publish_target(self) -> None:
        if _design_digest_at(self.candidate_stage) != self.value["output_design_digest"]:
            raise ValueError("candidate staging file is unavailable or invalid")
        if self.value["in_place"]:
            if _design_digest_at(self.target) != self.value["input_design_digest"]:
                raise ValueError("design changed before candidate publication")
            os.replace(self.candidate_stage, self.target)
        else:
            if self.target.exists():
                raise FileExistsError(f"design output already exists: {self.target}")
            os.link(self.candidate_stage, self.target)
        _fsync_directory(self.target.parent)
        self._set_status("target_published")
        _patch_crash_point("target_published")

    def publish_receipt(self) -> None:
        if _receipt_digest_at(self.receipt_stage) != self.value["receipt_digest"]:
            raise ValueError("receipt staging file is unavailable or invalid")
        if self.receipt.exists():
            raise FileExistsError(f"transaction receipt already exists: {self.receipt}")
        os.link(self.receipt_stage, self.receipt)
        _fsync_directory(self.receipt.parent)
        self._set_status("receipt_published")
        _patch_crash_point("receipt_published")

    def mark_approval_consumed(self) -> None:
        self._set_status("approval_consumed")
        _patch_crash_point("approval_consumed")

    def _status_at_least(self, status: str) -> bool:
        return _JOURNAL_STATUSES.index(self.value["status"]) >= _JOURNAL_STATUSES.index(
            status
        )

    def rollback(self) -> None:
        receipt_state = _receipt_digest_at(self.receipt)
        if receipt_state == self.value["receipt_digest"]:
            self.receipt.unlink()
            _fsync_directory(self.receipt.parent)
        elif receipt_state is not None and self._status_at_least(
            "receipt_published"
        ):
            raise RuntimeError("published receipt no longer matches the patch journal")

        target_state = _design_digest_at(self.target)
        if self.value["in_place"]:
            if target_state == self.value["output_design_digest"]:
                if self.backup is None or _design_digest_at(self.backup) != self.value[
                    "input_design_digest"
                ]:
                    raise RuntimeError("valid transaction backup is unavailable")
                os.replace(self.backup, self.target)
                _fsync_directory(self.target.parent)
            elif target_state is None:
                if self.backup is None or _design_digest_at(self.backup) != self.value[
                    "input_design_digest"
                ]:
                    raise RuntimeError("missing design cannot be recovered")
                os.replace(self.backup, self.target)
                _fsync_directory(self.target.parent)
            elif target_state != self.value["input_design_digest"]:
                if self._status_at_least("target_published"):
                    raise RuntimeError(
                        "published design no longer matches the patch journal"
                    )
        else:
            if target_state == self.value["output_design_digest"]:
                self.target.unlink()
                _fsync_directory(self.target.parent)
            elif target_state is not None and self._status_at_least(
                "target_published"
            ):
                raise RuntimeError("published design output is corrupt or replaced")
        self.cleanup()

    def cleanup(self) -> None:
        errors: list[Exception] = []
        entries = (
            (
                self.candidate_stage,
                self.value["output_design_digest"],
                _design_digest_at,
            ),
            (
                self.receipt_stage,
                self.value["receipt_digest"],
                _receipt_digest_at,
            ),
            (self.backup, self.value["input_design_digest"], _design_digest_at),
        )
        for path, expected, inspect in entries:
            if path is None:
                continue
            state = inspect(path)
            if state is None:
                continue
            if state != expected:
                if self.value["status"] != "preparing":
                    errors.append(
                        RuntimeError(f"patch staging file no longer matches: {path}")
                    )
                continue
            try:
                path.unlink()
                _fsync_directory(path.parent)
            except OSError as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError("failed to clean patch transaction staging") from errors[0]
        try:
            self.path.unlink()
            _fsync_directory(self.path.parent)
        except OSError as exc:
            raise RuntimeError("failed to remove completed patch journal") from exc


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            process_query_limited_information = 0x1000
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [
                ctypes.c_uint32,
                ctypes.c_int,
                ctypes.c_uint32,
            ]
            kernel32.OpenProcess.restype = ctypes.c_void_p
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_int
            handle = kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
            return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _pid_lock_value(path: Path) -> int:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 128:
        raise ValueError(f"patch lock is malformed: {path}")
    try:
        content = path.read_text(encoding="ascii")
    except UnicodeError as exc:
        raise ValueError(f"patch lock is malformed: {path}") from exc
    match = re.fullmatch(r"pid=([1-9][0-9]*)\r?\n", content)
    if match is None:
        raise ValueError(f"patch lock is malformed: {path}")
    return int(match.group(1))


def _remove_dead_lock(path: Path) -> None:
    if not path.exists():
        return
    pid = _pid_lock_value(path)
    if _pid_alive(pid):
        raise RuntimeError(f"patch transaction lock is still owned by PID {pid}")
    path.unlink()
    _fsync_directory(path.parent)


class _PidFileLock:
    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        self.message = message

    def __enter__(self) -> "_PidFileLock":
        try:
            with self.path.open("xb") as handle:
                handle.write(f"pid={os.getpid()}\n".encode("ascii"))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise RuntimeError(self.message) from exc
        _fsync_directory(self.path.parent)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        with contextlib.suppress(OSError):
            self.path.unlink(missing_ok=True)
            _fsync_directory(self.path.parent)


def _validate_recovery_receipt(
    journal: _DurablePatchJournal, path: Path
) -> dict[str, Any]:
    value = _validate_transaction_receipt(_read_json(path, "transaction receipt"))
    expectation = journal.value["approval_expectation"]
    checks = {
        "transaction_id": journal.value["transaction_id"],
        "approval_id": journal.value["approval_id"],
        "operation": journal.value["operation"],
        "design_id": expectation["design_id"],
        "input_revision": expectation["base_revision"],
        "input_design_digest": expectation["design_digest"],
        "output_design_digest": expectation["candidate_digest"],
        "patch_digest": expectation["patch_digest"],
        "source_netlist_regenerated": expectation["regenerate_source_netlist"],
        "source_transaction_digest": expectation["source_transaction_digest"],
    }
    for key, expected in checks.items():
        if value.get(key) != expected:
            raise ValueError(f"recovery receipt does not match journal {key}")
    if value["patch"].get("patch_id") != expectation["patch_id"]:
        raise ValueError("recovery receipt patch_id does not match approval")
    if _digest(value) != journal.value["receipt_digest"]:
        raise ValueError("recovery receipt digest does not match journal")
    return value


def _validate_recovery_approval(
    store: PatchApprovalStore, journal: _DurablePatchJournal
) -> dict[str, Any]:
    record = store._read_record(journal.value["approval_id"])
    expectation = journal.value["approval_expectation"]
    for key, expected in expectation.items():
        if record.get(key) != expected:
            raise ValueError(f"recovery approval does not match journal {key}")
    if record["status"] == "consumed" and record["transaction_id"] != journal.value[
        "transaction_id"
    ]:
        raise ValueError("approval was consumed by another transaction")
    return record


def find_patch_transaction_journals(
    target_path: str | os.PathLike[str],
) -> tuple[str, ...]:
    target = Path(target_path).expanduser().resolve()
    if target == Path(target.anchor) or not target.parent.is_dir():
        raise ValueError("patch recovery target is invalid")
    prefix = f".{target.name}.patch-journal-"
    suffix = ".multisim-patch-journal.json"
    matches: list[str] = []
    for candidate in target.parent.iterdir():
        if not candidate.name.startswith(prefix) or not candidate.name.endswith(suffix):
            continue
        try:
            journal = _DurablePatchJournal.read(candidate)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"invalid patch journal adjacent to target: {candidate}"
            ) from exc
        if journal.target == target:
            matches.append(str(journal.path))
    return tuple(sorted(matches))


def recover_patch_transaction(
    *,
    journal_path: str | None = None,
    target_path: str | None = None,
    action: str = "auto",
    approval_store: str | None = None,
) -> dict[str, Any]:
    """Recover or safely roll back one abandoned durable patch journal."""
    if (journal_path is None) == (target_path is None):
        raise ValueError("select exactly one of journal_path or target_path")
    if action not in {"auto", "commit", "rollback"}:
        raise ValueError("patch recovery action must be auto, commit, or rollback")
    if target_path is not None:
        matches = find_patch_transaction_journals(target_path)
        if not matches:
            raise FileNotFoundError("no patch transaction journal was found")
        if len(matches) != 1:
            raise RuntimeError("multiple patch journals found; select one explicitly")
        journal_path = matches[0]
    assert journal_path is not None
    journal = _DurablePatchJournal.read(journal_path)
    owner_pid = journal.value["owner_pid"]
    if _pid_alive(owner_pid):
        raise RuntimeError(
            f"patch journal owner PID {owner_pid} is still running; recovery refused"
        )
    stored_root = Path(journal.value["approval_store_path"])
    if approval_store is not None:
        requested_root = Path(approval_store).expanduser().resolve()
        if requested_root != stored_root:
            raise ValueError("approval store does not match the patch journal")
    store = PatchApprovalStore(stored_root)
    approval_lock = store.root / f".{journal.value['approval_id']}.lock"
    target_lock = journal.target.with_name(
        f".{journal.target.name}.multisim-patch.lock"
    )
    _remove_dead_lock(approval_lock)
    _remove_dead_lock(target_lock)
    with _PidFileLock(approval_lock, "patch approval recovery is already running"):
        with _TargetLock(journal.target):
            current = _DurablePatchJournal.read(journal.path)
            if _digest(current.value) != _digest(journal.value):
                raise ValueError("patch journal changed while recovery was starting")
            journal = current
            record = _validate_recovery_approval(store, journal)
            target_state = _design_digest_at(journal.target)
            receipt_state = _receipt_digest_at(journal.receipt)
            if receipt_state == journal.value["receipt_digest"]:
                _validate_recovery_receipt(journal, journal.receipt)
            if action == "auto":
                fully_published = (
                    target_state == journal.value["output_design_digest"]
                    and receipt_state == journal.value["receipt_digest"]
                )
                if record["status"] == "consumed" and not fully_published:
                    raise RuntimeError(
                        "consumed approval has incomplete publication; manual review required"
                    )
                action = "commit" if fully_published else "rollback"

            if action == "rollback":
                if record["status"] == "consumed":
                    raise RuntimeError("a consumed patch approval cannot be rolled back")
                journal.rollback()
                return {
                    "schema_version": PATCH_JOURNAL_SCHEMA_VERSION,
                    "command": "patch-recover",
                    "success": True,
                    "action": "rollback",
                    "transaction_id": journal.value["transaction_id"],
                    "approval_id": journal.value["approval_id"],
                    "approval_consumed": False,
                    "target": str(journal.target),
                    "receipt": str(journal.receipt),
                    "journal_removed": not journal.path.exists(),
                    "simulation_performed": False,
                }

            target_state = _design_digest_at(journal.target)
            if target_state != journal.value["output_design_digest"]:
                if journal.value["in_place"]:
                    if target_state != journal.value["input_design_digest"]:
                        raise RuntimeError("current design is unsafe to resume")
                elif target_state is not None:
                    raise RuntimeError("design output is unsafe to resume")
                journal.publish_target()
            elif not journal._status_at_least("target_published"):
                journal._set_status("target_published")

            receipt_state = _receipt_digest_at(journal.receipt)
            if receipt_state != journal.value["receipt_digest"]:
                if receipt_state is not None:
                    raise RuntimeError("transaction receipt is unsafe to resume")
                _validate_recovery_receipt(journal, journal.receipt_stage)
                journal.publish_receipt()
            else:
                _validate_recovery_receipt(journal, journal.receipt)
                if not journal._status_at_least("receipt_published"):
                    journal._set_status("receipt_published")

            consumed_now = store._consume_recovered(
                journal.value["approval_id"],
                journal.value["transaction_id"],
                journal.value["approval_expectation"],
            )
            cleanup_pending = False
            try:
                journal.mark_approval_consumed()
                journal.cleanup()
            except Exception:
                cleanup_pending = True
            return {
                "schema_version": PATCH_JOURNAL_SCHEMA_VERSION,
                "command": "patch-recover",
                "success": True,
                "action": "commit",
                "transaction_id": journal.value["transaction_id"],
                "approval_id": journal.value["approval_id"],
                "approval_consumed": True,
                "approval_consumed_during_recovery": consumed_now,
                "target": str(journal.target),
                "receipt": str(journal.receipt),
                "journal_removed": not journal.path.exists(),
                "recovery_required": cleanup_pending,
                "simulation_performed": False,
            }


def _transaction_receipt(
    *,
    operation: str,
    approval_id: str,
    design: CircuitDesign,
    prepared: PreparedDesignPatch,
    source_transaction_digest: str | None,
) -> dict[str, Any]:
    transaction_id = f"patch-txn-{uuid.uuid4().hex}"
    return {
        "schema_version": PATCH_TRANSACTION_SCHEMA_VERSION,
        "kind": "multisim-mcp-design-patch-transaction",
        "transaction_id": transaction_id,
        "operation": operation,
        "approval_id": approval_id,
        "applied_at": _timestamp(_utc_now()),
        "design_id": design.design_id,
        "input_revision": design.revision,
        "output_revision": prepared.candidate.revision,
        "input_design_digest": _digest(design.to_dict()),
        "output_design_digest": _digest(prepared.candidate.to_dict()),
        "patch": prepared.patch.to_dict(),
        "patch_digest": _digest(prepared.patch.to_dict()),
        "inverse_patch": prepared.inverse_patch.to_dict(),
        "source_netlist_regenerated": prepared.source_netlist_regenerated,
        "source_transaction_digest": source_transaction_digest,
    }


def _validate_transaction_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "kind",
        "transaction_id",
        "operation",
        "approval_id",
        "applied_at",
        "design_id",
        "input_revision",
        "output_revision",
        "input_design_digest",
        "output_design_digest",
        "patch",
        "patch_digest",
        "inverse_patch",
        "source_netlist_regenerated",
        "source_transaction_digest",
    }
    if set(value) != allowed:
        raise ValueError("transaction receipt fields are invalid")
    if value.get("schema_version") != PATCH_TRANSACTION_SCHEMA_VERSION:
        raise ValueError("transaction receipt schema_version must be 1")
    if value.get("kind") != "multisim-mcp-design-patch-transaction":
        raise ValueError("transaction receipt kind is invalid")
    if not isinstance(value.get("transaction_id"), str) or not _TRANSACTION_ID_RE.fullmatch(value["transaction_id"]):
        raise ValueError("transaction receipt transaction_id is invalid")
    if value.get("operation") not in {"apply", "revert"}:
        raise ValueError("transaction receipt operation is invalid")
    _parse_timestamp(value.get("applied_at"), "applied_at")
    approval_id = value.get("approval_id")
    if not isinstance(approval_id, str) or not _APPROVAL_ID_RE.fullmatch(approval_id):
        raise ValueError("transaction receipt approval_id is invalid")
    for key in ("input_design_digest", "output_design_digest", "patch_digest"):
        if not isinstance(value.get(key), str) or not _SHA256_RE.fullmatch(value[key]):
            raise ValueError(f"transaction receipt {key} is invalid")
    input_revision = value.get("input_revision")
    output_revision = value.get("output_revision")
    if (
        isinstance(input_revision, bool)
        or not isinstance(input_revision, int)
        or input_revision < 0
        or isinstance(output_revision, bool)
        or not isinstance(output_revision, int)
        or output_revision != input_revision + 1
    ):
        raise ValueError("transaction receipt revisions are invalid")
    if not isinstance(value.get("source_netlist_regenerated"), bool):
        raise ValueError("transaction receipt source regeneration flag is invalid")
    source_digest = value.get("source_transaction_digest")
    if source_digest is not None and (
        not isinstance(source_digest, str) or not _SHA256_RE.fullmatch(source_digest)
    ):
        raise ValueError("transaction receipt source_transaction_digest is invalid")
    patch = DesignPatch.from_dict(value.get("patch"))
    inverse = DesignPatch.from_dict(value.get("inverse_patch"))
    if _digest(patch.to_dict()) != value.get("patch_digest"):
        raise ValueError("transaction receipt patch digest mismatch")
    if patch.design_id != value.get("design_id") or patch.base_revision != input_revision:
        raise ValueError("transaction receipt patch identity mismatch")
    if inverse.design_id != value.get("design_id"):
        raise ValueError("transaction receipt inverse design mismatch")
    if inverse.to_dict() != patch.inverse().to_dict():
        raise ValueError("transaction receipt inverse patch mismatch")
    return dict(value)


def read_transaction_receipt(path: str | os.PathLike[str]) -> tuple[Path, dict[str, Any]]:
    source = _existing_file(path, "transaction receipt")
    return source, _validate_transaction_receipt(_read_json(source, "transaction receipt"))


def approve_patch_apply(
    design_path: str,
    patch_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    approval_store: str | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
    authorization_context_digest: str | None = None,
) -> dict[str, Any]:
    design_source, design = read_design_document(design_path)
    patch_source, patch = read_patch_document(patch_path)
    target = _target_path(design_source, output_path, in_place)
    receipt = _receipt_path(receipt_path, design_source, patch_source, target)
    prepared = _prepared_for_persistence(design, patch, regenerate_source_netlist)
    expectation = _approval_expectation(
        operation="apply",
        design_path=design_source,
        design=design,
        patch=patch,
        prepared=prepared,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        source_transaction_digest=None,
        authorization_context_digest=authorization_context_digest,
    )
    result = PatchApprovalStore(approval_store).create(
        expectation, ttl_seconds=ttl_seconds
    )
    result.update(
        {
            "command": "patch-approve",
            "target_in_place": target == design_source,
            "source_netlist_regenerated": prepared.source_netlist_regenerated,
            "authorization_context_digest": authorization_context_digest,
        }
    )
    return result


def approve_patch_revert(
    design_path: str,
    transaction_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    approval_store: str | None = None,
    ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS,
) -> dict[str, Any]:
    design_source, design = read_design_document(design_path)
    transaction_source, transaction = read_transaction_receipt(transaction_path)
    if _digest(design.to_dict()) != transaction["output_design_digest"]:
        raise ValueError("current design does not match the transaction output")
    patch = DesignPatch.from_dict(transaction["inverse_patch"])
    target = _target_path(design_source, output_path, in_place)
    receipt = _receipt_path(receipt_path, design_source, transaction_source, target)
    prepared = _prepared_for_persistence(design, patch, regenerate_source_netlist)
    source_digest = _digest(transaction)
    expectation = _approval_expectation(
        operation="revert",
        design_path=design_source,
        design=design,
        patch=patch,
        prepared=prepared,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        source_transaction_digest=source_digest,
        authorization_context_digest=None,
    )
    result = PatchApprovalStore(approval_store).create(
        expectation, ttl_seconds=ttl_seconds
    )
    result.update(
        {
            "command": "patch-approve",
            "target_in_place": target == design_source,
            "source_netlist_regenerated": prepared.source_netlist_regenerated,
            "source_transaction_id": transaction["transaction_id"],
        }
    )
    return result


def _execute_patch_transaction(
    *,
    operation: str,
    design_source: Path,
    design: CircuitDesign,
    patch: DesignPatch,
    target: Path,
    receipt: Path,
    regenerate_source_netlist: bool,
    approval_token: str,
    approval_store: str | None,
    source_transaction_digest: str | None,
    authorization_context_digest: str | None,
) -> dict[str, Any]:
    prepared = _prepared_for_persistence(design, patch, regenerate_source_netlist)
    expectation = _approval_expectation(
        operation=operation,
        design_path=design_source,
        design=design,
        patch=patch,
        prepared=prepared,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        source_transaction_digest=source_transaction_digest,
        authorization_context_digest=authorization_context_digest,
    )
    store = PatchApprovalStore(approval_store)
    journal_path: Path | None = None
    journal_cleanup_pending = False
    with store.claim(approval_token, expectation) as claim:
        with _TargetLock(target):
            # Re-read only after both cross-process locks are held. Separate
            # approvals for the same target therefore cannot race publication.
            current_path, current_design = read_design_document(str(design_source))
            if (
                current_path != design_source
                or _digest(current_design.to_dict()) != expectation["design_digest"]
            ):
                raise ValueError("design changed after approval")
            if target != design_source and target.exists():
                raise FileExistsError(f"design output already exists: {target}")
            if receipt.exists():
                raise FileExistsError(f"transaction receipt already exists: {receipt}")
            receipt_value = _transaction_receipt(
                operation=operation,
                approval_id=claim.record["approval_id"],
                design=design,
                prepared=prepared,
                source_transaction_digest=source_transaction_digest,
            )
            journal: _DurablePatchJournal | None = None
            try:
                journal = _DurablePatchJournal.create(
                    design_source=design_source,
                    target=target,
                    receipt=receipt,
                    approval_store=store.root,
                    approval_id=claim.record["approval_id"],
                    operation=operation,
                    expectation=expectation,
                    receipt_value=receipt_value,
                )
                journal_path = journal.path
                journal.prepare(
                    _json_bytes(prepared.candidate.to_dict()),
                    _json_bytes(receipt_value),
                )
                journal.publish_target()
                journal.publish_receipt()
                claim.consume(receipt_value["transaction_id"])
            except Exception as exc:
                if journal is not None:
                    try:
                        journal.rollback()
                    except Exception as rollback_exc:
                        raise RuntimeError(
                            "patch transaction rollback requires recovery from "
                            f"journal: {journal.path}"
                        ) from rollback_exc
                raise
            try:
                journal.mark_approval_consumed()
                journal.cleanup()
            except Exception:
                # The design, receipt, and approval are already committed.
                # Preserve the journal so patch-recover can verify and clean it.
                journal_cleanup_pending = True
    return {
        "schema_version": PATCH_TRANSACTION_SCHEMA_VERSION,
        "command": "patch-apply" if operation == "apply" else "patch-revert",
        "success": True,
        "transaction_id": receipt_value["transaction_id"],
        "operation": operation,
        "design_id": prepared.candidate.design_id,
        "input_revision": design.revision,
        "output_revision": prepared.candidate.revision,
        "output": str(target),
        "receipt": str(receipt),
        "output_design_digest": receipt_value["output_design_digest"],
        "approval_id": receipt_value["approval_id"],
        "approval_consumed": True,
        "journal": {
            "path": str(journal_path) if journal_path is not None else None,
            "retained": journal_cleanup_pending,
            "recovery_required": journal_cleanup_pending,
        },
        "source_netlist_regenerated": prepared.source_netlist_regenerated,
        "backend_called": False,
        "simulation_performed": False,
        "electrical_correctness_proven": False,
        "authorization_context_digest": authorization_context_digest,
    }


def validate_patch_apply_approval(
    design_path: str,
    patch_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    approval_token: str,
    approval_store: str | None = None,
    authorization_context_digest: str | None = None,
) -> dict[str, Any]:
    """Validate one exact apply approval without consuming its bearer token."""
    design_source, design = read_design_document(design_path)
    patch_source, patch = read_patch_document(patch_path)
    target = _target_path(design_source, output_path, in_place)
    receipt = _receipt_path(receipt_path, design_source, patch_source, target)
    prepared = _prepared_for_persistence(design, patch, regenerate_source_netlist)
    expectation = _approval_expectation(
        operation="apply",
        design_path=design_source,
        design=design,
        patch=patch,
        prepared=prepared,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        source_transaction_digest=None,
        authorization_context_digest=authorization_context_digest,
    )
    with PatchApprovalStore(approval_store).claim(approval_token, expectation) as claim:
        return {
            "schema_version": PATCH_APPROVAL_SCHEMA_VERSION,
            "success": True,
            "approval_id": claim.record["approval_id"],
            "expires_at": claim.record["expires_at"],
            "design_id": design.design_id,
            "candidate_digest": expectation["candidate_digest"],
            "target": str(target),
            "receipt": str(receipt),
            "approval_consumed": False,
            "authorization_context_digest": authorization_context_digest,
        }


def apply_patch_transaction(
    design_path: str,
    patch_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    approval_token: str,
    approval_store: str | None = None,
    authorization_context_digest: str | None = None,
) -> dict[str, Any]:
    design_source, design = read_design_document(design_path)
    patch_source, patch = read_patch_document(patch_path)
    target = _target_path(design_source, output_path, in_place)
    receipt = _receipt_path(receipt_path, design_source, patch_source, target)
    return _execute_patch_transaction(
        operation="apply",
        design_source=design_source,
        design=design,
        patch=patch,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        approval_token=approval_token,
        approval_store=approval_store,
        source_transaction_digest=None,
        authorization_context_digest=authorization_context_digest,
    )


def revert_patch_transaction(
    design_path: str,
    transaction_path: str,
    *,
    output_path: str | None,
    in_place: bool,
    receipt_path: str,
    regenerate_source_netlist: bool,
    approval_token: str,
    approval_store: str | None = None,
) -> dict[str, Any]:
    design_source, design = read_design_document(design_path)
    transaction_source, transaction = read_transaction_receipt(transaction_path)
    if _digest(design.to_dict()) != transaction["output_design_digest"]:
        raise ValueError("current design does not match the transaction output")
    patch = DesignPatch.from_dict(transaction["inverse_patch"])
    target = _target_path(design_source, output_path, in_place)
    receipt = _receipt_path(receipt_path, design_source, transaction_source, target)
    return _execute_patch_transaction(
        operation="revert",
        design_source=design_source,
        design=design,
        patch=patch,
        target=target,
        receipt=receipt,
        regenerate_source_netlist=regenerate_source_netlist,
        approval_token=approval_token,
        approval_store=approval_store,
        source_transaction_digest=_digest(transaction),
        authorization_context_digest=None,
    )


__all__ = [
    "DEFAULT_APPROVAL_TTL_SECONDS",
    "PATCH_APPROVAL_STORE_ENV",
    "PATCH_JOURNAL_SCHEMA_VERSION",
    "PatchApprovalStore",
    "apply_patch_transaction",
    "approve_patch_apply",
    "approve_patch_revert",
    "find_patch_transaction_journals",
    "read_design_document",
    "read_approval_token",
    "read_patch_document",
    "read_transaction_receipt",
    "recover_patch_transaction",
    "revert_patch_transaction",
    "validate_patch_apply_approval",
    "write_approval_token",
]
