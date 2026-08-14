"""Redaction for diagnostics leaving a Raspberry Pi.

Applied on the agent, before anything is written into a bundle: redacting at the
registry would already be too late, because the unredacted bytes would have
crossed the network and touched a registry temp file.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

REDACTION_VERSION = 1

PLACEHOLDER = "[redacted:{label}]"

_PEM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_ENROLLMENT_CODE = re.compile(r"TAKT-[A-Za-z0-9_-]{8,}")
_BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
_CREDENTIAL_URL = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")
# Matches `key = value`, `key: value`, `key="value"` and `"key": "value"` for a
# secret-ish key name, in TOML, INI, JSON and log lines alike.
_KEYED_SECRET = re.compile(
    r"(?i)(\"?\b\w*(?:password|passwd|passphrase|psk|secret|token|api[_-]?key)\w*\b\"?\s*[:=]\s*)"
    r"(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;}\n]+)"
)

_SECRET_KEY_NAMES = re.compile(
    r"(?i)\b\w*(?:password|passwd|passphrase|psk|secret|token|credential|api[_-]?key"
    r"|lease_id|lease_token)\w*\b"
)


def _placeholder(label: str) -> str:
    return PLACEHOLDER.format(label=label)


def redact_text(text: str, *, secrets: Sequence[str] = ()) -> str:
    """Redact secrets from free text (log lines, command output, config files)."""
    result = str(text)
    # Exact known secrets first: the agent knows its own device token and lease
    # ids, and an exact match is far more reliable than any pattern.
    for secret in secrets:
        if secret and len(str(secret)) >= 8:
            result = result.replace(str(secret), _placeholder("secret"))
    result = _PEM_BLOCK.sub(_placeholder("private-key"), result)
    result = _CREDENTIAL_URL.sub(rf"\1{_placeholder('url-credentials')}@", result)
    result = _BEARER.sub(rf"\1 {_placeholder('bearer-token')}", result)
    result = _KEYED_SECRET.sub(rf"\1{_placeholder('secret')}", result)
    result = _ENROLLMENT_CODE.sub(_placeholder("enrollment-code"), result)
    return result


def redact_mapping(value: Any, *, secrets: Sequence[str] = ()) -> Any:
    """Redact a parsed structure by key name, recursively.

    Used for TOML/JSON configuration whose raw text is never shipped: matching on
    the key is reliable in a way that pattern-matching the serialized form is not.
    """
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _SECRET_KEY_NAMES.search(key):
                redacted[key] = _placeholder("secret")
            else:
                redacted[key] = redact_mapping(item, secrets=secrets)
        return redacted
    if isinstance(value, list):
        return [redact_mapping(item, secrets=secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets=secrets)
    return value
