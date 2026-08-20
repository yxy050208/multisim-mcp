"""Safe model-provider configuration for the future local workbench.

The Multisim MCP server does not need a model API key.  This module belongs to
the orchestration boundary: it records only environment-variable references and
keeps credential values out of configuration files, logs, and experiment data.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

PROVIDER_CONFIG_SCHEMA_VERSION = 1
PROVIDER_CONFIG_ENV = "MULTISIM_MODEL_PROVIDER_CONFIG"

_PROVIDER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}

_PRESETS: dict[str, dict[str, str | None]] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "model_env": "DEEPSEEK_MODEL",
        "models_path": "/models",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "model": None,
        "api_key_env": "OPENAI_API_KEY",
        "base_url_env": "OPENAI_BASE_URL",
        "model_env": "OPENAI_MODEL",
        "models_path": "/models",
    },
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "model": None,
        "api_key_env": None,
        "base_url_env": "OLLAMA_BASE_URL",
        "model_env": "OLLAMA_MODEL",
        "models_path": "/models",
    },
    "openai-compatible": {
        "base_url": None,
        "model": None,
        "api_key_env": "OPENAI_COMPATIBLE_API_KEY",
        "base_url_env": "OPENAI_COMPATIBLE_BASE_URL",
        "model_env": "OPENAI_COMPATIBLE_MODEL",
        "models_path": "/models",
    },
}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a provider credential through an HTTP redirect."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        return None


def _open_without_redirect(
    request: urllib.request.Request, timeout: float
) -> Any:
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def default_provider_config_path(
    environ: Mapping[str, str] | None = None,
    *,
    os_name: str | None = None,
) -> Path:
    """Return the per-user provider configuration path."""
    env = os.environ if environ is None else environ
    configured = str(env.get(PROVIDER_CONFIG_ENV, "")).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    platform = os.name if os_name is None else os_name
    if platform == "nt":
        root = str(env.get("LOCALAPPDATA", "")).strip()
        base = Path(root) if root else Path.home() / "AppData" / "Local"
    else:
        root = str(env.get("XDG_CONFIG_HOME", "")).strip()
        base = Path(root).expanduser() if root else Path.home() / ".config"
    return (base / "multisim-mcp" / "providers.json").resolve()


def _clean_text(value: str, field: str, *, required: bool = True) -> str:
    text = str(value).strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    if _CONTROL_RE.search(text):
        raise ValueError(f"{field} must not contain control characters")
    return text


def _normalize_base_url(value: str) -> str:
    text = _clean_text(value, "base URL")
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base URL must not contain credentials")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("base URL contains an invalid port") from exc
    if parsed.query or parsed.fragment:
        raise ValueError("base URL must not contain a query or fragment")
    if parsed.scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise ValueError("plain HTTP is allowed only for a loopback provider")
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _normalize_models_path(value: str) -> str:
    path = _clean_text(value, "models path")
    if not path.startswith("/") or "?" in path or "#" in path or "\\" in path:
        raise ValueError("models path must be an absolute URL path")
    if ".." in path.split("/"):
        raise ValueError("models path must not contain parent traversal")
    return path


def build_provider(
    provider: str,
    *,
    provider_id: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    api_key_env: str | None = None,
    models_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build and validate one provider without resolving its credential value."""
    env = os.environ if environ is None else environ
    if provider not in _PRESETS:
        raise ValueError(f"unsupported provider: {provider}")
    preset = _PRESETS[provider]
    name = provider if provider_id is None else provider_id
    if not _PROVIDER_ID_RE.fullmatch(name):
        raise ValueError(
            "provider ID must be 1-64 letters, digits, dots, underscores, or hyphens"
        )

    base_env = str(preset["base_url_env"])
    model_env = str(preset["model_env"])
    resolved_base = (
        base_url
        if base_url is not None
        else env.get(base_env) or preset["base_url"]
    )
    resolved_model = (
        model if model is not None else env.get(model_env) or preset["model"]
    )
    if not resolved_base:
        raise ValueError(f"base URL is required for provider {name}")
    if not resolved_model:
        raise ValueError(
            f"model is required for provider {name}; pass --model or set {model_env}"
        )
    model_value = _clean_text(str(resolved_model), "model")
    if len(model_value) > 256:
        raise ValueError("model must not exceed 256 characters")

    if api_key_env is None:
        configured_key_env = preset["api_key_env"]
    else:
        configured_key_env = api_key_env.strip() or None
    if configured_key_env is not None and not _ENV_NAME_RE.fullmatch(
        str(configured_key_env)
    ):
        raise ValueError("API key environment variable name is invalid")

    result: dict[str, Any] = {
        "id": name,
        "provider": provider,
        "api_format": "openai-compatible",
        "base_url": _normalize_base_url(str(resolved_base)),
        "model": model_value,
        "models_path": _normalize_models_path(
            str(preset["models_path"]) if models_path is None else models_path
        ),
    }
    if configured_key_env:
        result["credential"] = {
            "source": "environment",
            "name": str(configured_key_env),
        }
    else:
        result["credential"] = None
    return result


def make_provider_config(
    providers: list[dict[str, Any]], active_provider: str | None = None
) -> dict[str, Any]:
    """Create a versioned provider document and validate every entry."""
    by_id: dict[str, dict[str, Any]] = {}
    for raw in providers:
        provider = validate_provider(raw)
        provider_id = provider["id"]
        if provider_id in by_id:
            raise ValueError(f"duplicate provider ID: {provider_id}")
        by_id[provider_id] = provider
    selected = active_provider or (next(iter(by_id)) if len(by_id) == 1 else None)
    if selected is not None and selected not in by_id:
        raise ValueError(f"active provider does not exist: {selected}")
    return {
        "schema_version": PROVIDER_CONFIG_SCHEMA_VERSION,
        "active_provider": selected,
        "providers": by_id,
    }


def validate_provider(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a serialized provider while rejecting unknown secret fields."""
    forbidden = {
        key for key in raw if key.lower() in {"api_key", "apikey", "token", "secret"}
    }
    if forbidden:
        raise ValueError("provider config must not contain plaintext credential fields")
    allowed = {
        "id",
        "provider",
        "api_format",
        "base_url",
        "model",
        "models_path",
        "credential",
    }
    unknown = set(raw) - allowed
    if unknown:
        raise ValueError(f"unknown provider fields: {', '.join(sorted(unknown))}")
    if raw.get("api_format", "openai-compatible") != "openai-compatible":
        raise ValueError("api_format must be openai-compatible")
    provider_id = str(raw.get("id", ""))
    if not provider_id:
        raise ValueError("provider ID is required")
    provider = str(raw.get("provider", ""))
    credential = raw.get("credential")
    api_key_env: str | None
    if credential is None:
        api_key_env = ""
    elif isinstance(credential, Mapping):
        if set(credential) != {"source", "name"}:
            raise ValueError(
                "credential must contain only source and environment variable name"
            )
        if credential.get("source") != "environment":
            raise ValueError("credential source must be environment")
        api_key_env = str(credential.get("name", ""))
    else:
        raise ValueError("credential must be null or an environment reference")
    return build_provider(
        provider,
        provider_id=provider_id,
        base_url=str(raw.get("base_url", "")),
        model=str(raw.get("model", "")),
        api_key_env=api_key_env,
        models_path=str(raw.get("models_path", "/models")),
        environ={},
    )


def validate_provider_config(raw: Mapping[str, Any]) -> dict[str, Any]:
    forbidden = {
        key for key in raw if key.lower() in {"api_key", "apikey", "token", "secret"}
    }
    if forbidden:
        raise ValueError("provider config must not contain plaintext credential fields")
    unknown = set(raw) - {"schema_version", "active_provider", "providers"}
    if unknown:
        raise ValueError(f"unknown provider config fields: {', '.join(sorted(unknown))}")
    if raw.get("schema_version") != PROVIDER_CONFIG_SCHEMA_VERSION:
        raise ValueError(
            f"provider config schema_version must be {PROVIDER_CONFIG_SCHEMA_VERSION}"
        )
    providers = raw.get("providers")
    if not isinstance(providers, Mapping):
        raise ValueError("providers must be an object")
    normalized: list[dict[str, Any]] = []
    for key, value in providers.items():
        if not isinstance(value, Mapping):
            raise ValueError(f"provider {key!r} must be an object")
        entry = dict(value)
        if "id" in entry and entry["id"] != key:
            raise ValueError(f"provider key and ID differ: {key}")
        entry["id"] = key
        normalized.append(validate_provider(entry))
    active = raw.get("active_provider")
    if active is not None and not isinstance(active, str):
        raise ValueError("active_provider must be a string or null")
    if isinstance(active, str) and not active:
        raise ValueError("active_provider must not be empty")
    return make_provider_config(normalized, active_provider=active)


def discover_provider_config(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Discover complete provider settings without returning any secret value."""
    env = os.environ if environ is None else environ
    detected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    deepseek_key = bool(str(env.get("DEEPSEEK_API_KEY", "")).strip())
    if deepseek_key:
        detected.append(build_provider("deepseek", environ=env))

    openai_key = bool(str(env.get("OPENAI_API_KEY", "")).strip())
    if openai_key:
        if str(env.get("OPENAI_MODEL", "")).strip():
            detected.append(build_provider("openai", environ=env))
        else:
            skipped.append({"provider": "openai", "missing": ["OPENAI_MODEL"]})

    if str(env.get("OLLAMA_MODEL", "")).strip():
        detected.append(build_provider("ollama", environ=env))

    compatible_base = str(env.get("OPENAI_COMPATIBLE_BASE_URL", "")).strip()
    compatible_model = str(env.get("OPENAI_COMPATIBLE_MODEL", "")).strip()
    if compatible_base or compatible_model:
        missing = []
        if not compatible_base:
            missing.append("OPENAI_COMPATIBLE_BASE_URL")
        if not compatible_model:
            missing.append("OPENAI_COMPATIBLE_MODEL")
        if missing:
            skipped.append({"provider": "openai-compatible", "missing": missing})
        else:
            key_env = (
                "OPENAI_COMPATIBLE_API_KEY"
                if str(env.get("OPENAI_COMPATIBLE_API_KEY", "")).strip()
                else ""
            )
            detected.append(
                build_provider(
                    "openai-compatible", environ=env, api_key_env=key_env
                )
            )

    priority = ("deepseek", "openai", "ollama", "openai-compatible")
    ids = {item["id"] for item in detected}
    active = next((item for item in priority if item in ids), None)
    return {
        "config": make_provider_config(detected, active_provider=active),
        "detected": [item["id"] for item in detected],
        "skipped": skipped,
        "credential_values_exposed": False,
    }


def read_provider_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    source = (
        default_provider_config_path()
        if path is None
        else Path(path).expanduser().resolve()
    )
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read provider config: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("provider config root must be an object")
    return validate_provider_config(payload)


def write_provider_config(
    config: Mapping[str, Any], path: str | os.PathLike[str] | None = None
) -> Path:
    """Atomically write a validated, secret-free provider document."""
    normalized = validate_provider_config(config)
    target = (
        default_provider_config_path()
        if path is None
        else Path(path).expanduser().resolve()
    )
    if target == Path(target.anchor):
        raise ValueError("provider config path must not be a filesystem root")
    if target.exists() and not target.is_file():
        raise ValueError("provider config path must be a file")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(OSError):
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary is not None:
            with contextlib.suppress(OSError):
                temporary.unlink()
    return target


def _redact(text: str, secrets: list[str]) -> str:
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def probe_provider(
    provider: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Call the provider's models endpoint without exposing its credential."""
    item = validate_provider(provider)
    env = os.environ if environ is None else environ
    credential = item.get("credential")
    secret = ""
    if credential:
        variable = credential["name"]
        secret = str(env.get(variable, "")).strip()
        if not secret:
            return {
                "provider": item["id"],
                "success": False,
                "status": "missing_credential",
                "credential_env": variable,
            }
    endpoint = item["base_url"] + item["models_path"]
    headers = {"Accept": "application/json", "User-Agent": "multisim-mcp/1"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    request = urllib.request.Request(endpoint, headers=headers, method="GET")
    safe_endpoint = _redact(endpoint, [secret])
    try:
        with _open_without_redirect(request, timeout=timeout) as response:
            status_code = int(getattr(response, "status", 200))
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("models response exceeded 1 MiB")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, Mapping) or not isinstance(
            payload.get("data"), list
        ):
            raise ValueError("models response must contain a data array")
        rows = payload["data"]
        model_ids = sorted(
            str(row["id"])
            for row in rows
            if isinstance(row, Mapping) and isinstance(row.get("id"), str)
        )
        reachable = 200 <= status_code < 300
        model_available = item["model"] in model_ids
        success = reachable and model_available
        return {
            "provider": item["id"],
            "success": success,
            "status": (
                "ready"
                if success
                else "model_missing"
                if reachable
                else "http_error"
            ),
            "http_status": status_code,
            "endpoint": safe_endpoint,
            "configured_model": _redact(item["model"], [secret]),
            "model_available": model_available,
            "model_count": len(model_ids),
        }
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        status_code = exc.code if isinstance(exc, urllib.error.HTTPError) else None
        message = _redact(str(exc), [secret])
        return {
            "provider": item["id"],
            "success": False,
            "status": "http_error" if status_code else "unreachable",
            "http_status": status_code,
            "endpoint": safe_endpoint,
            "error": message,
        }


def probe_provider_config(
    config: Mapping[str, Any],
    *,
    provider_id: str | None = None,
    environ: Mapping[str, str] | None = None,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    normalized = validate_provider_config(config)
    providers = normalized["providers"]
    if provider_id:
        if provider_id not in providers:
            raise ValueError(f"provider does not exist: {provider_id}")
        selected = [providers[provider_id]]
    else:
        selected = list(providers.values())
    return [
        probe_provider(item, environ=environ, timeout=timeout) for item in selected
    ]
