"""Safe JSON-compatible payload serialization and redaction."""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime
from typing import Any, Callable, Pattern

REDACTED = "[REDACTED]"

DEFAULT_REDACTION_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "client_secret",
    "password",
    "refresh_token",
    "secret",
    "ssn",
    "token",
}

_MEMORY_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]+")

RedactionCallback = Callable[[str, str | None, Any], Any]


@dataclass(frozen=True)
class RedactionConfig:
    """Payload redaction configuration used before persistence."""

    json_pointers: tuple[str, ...] = ()
    keys: tuple[str, ...] = tuple(sorted(DEFAULT_REDACTION_KEYS))
    regexes: tuple[Pattern[str], ...] = ()
    callbacks: tuple[RedactionCallback, ...] = ()
    replacement: str = REDACTED

    @classmethod
    def from_rules(
        cls,
        rules: list[str | Pattern[str]] | tuple[str | Pattern[str], ...] | None = None,
        *,
        callbacks: list[RedactionCallback] | tuple[RedactionCallback, ...] | None = None,
        replacement: str = REDACTED,
        include_defaults: bool = True,
    ) -> "RedactionConfig":
        pointers: list[str] = []
        keys: set[str] = set(DEFAULT_REDACTION_KEYS if include_defaults else ())
        regexes: list[Pattern[str]] = []
        for rule in rules or ():
            if hasattr(rule, "search"):
                regexes.append(rule)  # type: ignore[arg-type]
                continue
            rendered_rule = str(rule)
            if rendered_rule.startswith("re:"):
                regexes.append(re.compile(rendered_rule[3:]))
            elif rendered_rule.startswith("/"):
                pointers.append(_canonical_json_pointer(rendered_rule))
            else:
                keys.add(rendered_rule.casefold())
        return cls(
            json_pointers=tuple(sorted(set(pointers))),
            keys=tuple(sorted(keys)),
            regexes=tuple(regexes),
            callbacks=tuple(callbacks or ()),
            replacement=replacement,
        )


@dataclass
class RedactionResult:
    value: Any
    redacted_paths: list[str] = field(default_factory=list)

    @property
    def redacted(self) -> bool:
        return bool(self.redacted_paths)

    def metadata(self) -> dict[str, Any]:
        if not self.redacted:
            return {"redacted": False}
        return {
            "redacted": True,
            "paths": sorted(set(self.redacted_paths)),
        }


@dataclass
class SerializedPayload:
    value: Any
    payload_hash: str
    preview: Any
    redaction: dict[str, Any]
    truncation: dict[str, Any]
    storage_bytes: int


def safe_serialize_for_storage(
    value: Any,
    *,
    redaction_config: RedactionConfig | None = None,
) -> RedactionResult:
    safe_value = safe_serialize(value)
    return redact_value(safe_value, redaction_config or RedactionConfig.from_rules())


def prepare_serialized_payload(
    value: Any,
    *,
    redaction_config: RedactionConfig | None = None,
    max_preview_chars: int = 2_000,
    max_blob_bytes: int | None = None,
) -> SerializedPayload:
    redaction_result = safe_serialize_for_storage(value, redaction_config=redaction_config)
    storage_value = redaction_result.value
    storage_bytes = len(canonical_serialize_for_hash(storage_value).encode("utf-8"))
    truncation = {"truncated": False}
    if max_blob_bytes is not None and storage_bytes > max_blob_bytes:
        storage_value = preview_serialize(storage_value, max_chars=max_preview_chars)
        truncation = {
            "truncated": True,
            "original_bytes": storage_bytes,
            "max_blob_bytes": max_blob_bytes,
        }
        storage_bytes = len(canonical_serialize_for_hash(storage_value).encode("utf-8"))

    return SerializedPayload(
        value=storage_value,
        payload_hash=hash_serialized_payload(storage_value),
        preview=preview_serialize(storage_value, max_chars=max_preview_chars),
        redaction=redaction_result.metadata(),
        truncation=truncation,
        storage_bytes=storage_bytes,
    )


def canonical_serialize_for_hash(value: Any) -> str:
    canonical_value = _canonicalize_for_hash(safe_serialize(value))
    return json.dumps(canonical_value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_serialized_payload(value: Any) -> str:
    return hashlib.sha256(canonical_serialize_for_hash(value).encode("utf-8")).hexdigest()


def preview_serialize(value: Any, *, max_chars: int = 2_000) -> Any:
    safe_value = safe_serialize(value)
    rendered = json.dumps(_canonicalize_for_hash(safe_value), sort_keys=True, ensure_ascii=False)
    if len(rendered) <= max_chars:
        return safe_value
    return {
        "truncated": True,
        "chars": len(rendered),
        "max_chars": max_chars,
        "preview": rendered[:max_chars],
    }


def redact_value(value: Any, config: RedactionConfig) -> RedactionResult:
    redacted_paths: list[str] = []

    def walk(item: Any, path: str, key: str | None = None) -> Any:
        callback_replacement = _callback_replacement(config, path, key, item)
        if callback_replacement is not _NO_REDACTION:
            redacted_paths.append(path)
            return callback_replacement
        if _matches_redaction_rule(config, path, key, item):
            redacted_paths.append(path)
            return config.replacement
        if isinstance(item, dict):
            return {
                safe_key: walk(child, _child_pointer(path, safe_key), safe_key)
                for safe_key, child in item.items()
            }
        if isinstance(item, list):
            return [walk(child, _child_pointer(path, str(index)), None) for index, child in enumerate(item)]
        return item

    return RedactionResult(value=walk(value, ""), redacted_paths=redacted_paths)


def safe_serialize(value: Any) -> Any:
    try:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        branchpoint_serialize = getattr(value, "__branchpoint_serialize__", None)
        if callable(branchpoint_serialize):
            return safe_serialize(branchpoint_serialize())
        if isinstance(value, bytes):
            return {"type": "bytes", "length": len(value)}
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Exception):
            return {"error_type": type(value).__name__, "error": str(value)}
        if is_dataclass(value) and not isinstance(value, type):
            return safe_serialize(asdict(value))
        if hasattr(value, "model_dump") and callable(value.model_dump):
            return safe_serialize(value.model_dump())
        if isinstance(value, dict):
            return {str(safe_serialize(key)): safe_serialize(item) for key, item in value.items()}
        if isinstance(value, set):
            return sorted(
                (safe_serialize(item) for item in value),
                key=lambda item: canonical_serialize_for_hash(item),
            )
        if isinstance(value, (list, tuple)):
            return [safe_serialize(item) for item in value]
        return {"type": type(value).__name__, "repr": _stable_repr(value)}
    except Exception as exc:
        try:
            rendered = _stable_repr(value)
        except Exception:
            rendered = "<unrepresentable>"
        return {
            "serialization_error": str(exc),
            "type": type(value).__name__,
            "repr": rendered,
        }


class _NoRedaction:
    pass


_NO_REDACTION = _NoRedaction()


def _callback_replacement(config: RedactionConfig, path: str, key: str | None, value: Any) -> Any:
    for callback in config.callbacks:
        try:
            replacement = callback(path, key, value)
        except Exception:
            replacement = True
        if replacement is True:
            return config.replacement
        if replacement not in (False, None):
            return safe_serialize(replacement)
    return _NO_REDACTION


def _matches_redaction_rule(config: RedactionConfig, path: str, key: str | None, value: Any) -> bool:
    if path in config.json_pointers:
        return True
    if key is not None and key.casefold() in config.keys:
        return True
    if isinstance(value, str):
        return any(regex.search(value) for regex in config.regexes)
    return False


def _canonicalize_for_hash(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize_for_hash(item) for key, item in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_canonicalize_for_hash(item) for item in value]
    return value


def _canonical_json_pointer(path: str) -> str:
    if path == "":
        return ""
    if not path.startswith("/"):
        raise ValueError("JSON Pointer redaction rules must start with '/'")
    return "/" + "/".join(_escape_pointer_segment(_unescape_pointer_segment(segment)) for segment in path[1:].split("/"))


def _child_pointer(path: str, segment: str) -> str:
    escaped = _escape_pointer_segment(segment)
    return f"/{escaped}" if path == "" else f"{path}/{escaped}"


def _escape_pointer_segment(segment: str) -> str:
    return str(segment).replace("~", "~0").replace("/", "~1")


def _unescape_pointer_segment(segment: str) -> str:
    index = 0
    while index < len(segment):
        if segment[index] == "~":
            if index + 1 >= len(segment) or segment[index + 1] not in {"0", "1"}:
                raise ValueError("Invalid JSON Pointer escape in redaction rule")
            index += 2
        else:
            index += 1
    return segment.replace("~1", "/").replace("~0", "~")


def _stable_repr(value: Any) -> str:
    return _MEMORY_ADDRESS_RE.sub("0xADDR", repr(value))[:1000]
