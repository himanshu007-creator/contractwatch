"""Small, safe filesystem helpers: atomic writes and restrictive permissions."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from .errors import ReportError


def ensure_dir(path: Path, *, mode: int = 0o700) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod(path, mode)


def atomic_write_text(path: Path, text: str, *, mode: int = 0o600) -> None:
    """Write ``text`` atomically: temp file in the same dir, then ``os.replace``."""
    _atomic(path, text.encode("utf-8"), mode)


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o600) -> None:
    payload = json.dumps(data, indent=2, sort_keys=False, default=str)
    _atomic(path, payload.encode("utf-8"), mode)


def _atomic(path: Path, payload: bytes, mode: int) -> None:
    ensure_dir(path.parent)
    handle = None
    tmp_name: str | None = None
    try:
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115 - closed explicitly before replace
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        )
        tmp_name = handle.name
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_name, path)
        _chmod(path, mode)
    except OSError as exc:
        if handle is not None:
            handle.close()
        if tmp_name is not None:
            with suppress(OSError):
                os.unlink(tmp_name)
        raise ReportError(f"Could not write {path}: {exc}") from exc


def _chmod(path: Path, mode: int) -> None:
    with suppress(OSError, NotImplementedError):
        os.chmod(path, mode)


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"Could not read {path}: {exc}") from exc


__all__ = ["atomic_write_json", "atomic_write_text", "ensure_dir", "read_json"]
