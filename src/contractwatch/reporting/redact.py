"""Redaction helpers so reports never persist secrets."""

from __future__ import annotations

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET_QUERY_KEYS = {"token", "access_token", "key", "api_key", "sig", "signature", "password"}


def redact(value: str) -> str:
    """One-way digest of a secret. Deterministic but not reversible."""
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]}"


def redact_url(url: str) -> str:
    """Strip credentials and secret query parameters from a URL."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    query = [(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _SECRET_QUERY_KEYS]
    return urlunsplit((parts.scheme, host, parts.path, urlencode(query), ""))


__all__ = ["redact", "redact_url"]
