"""Safe, content-addressed caching of OpenAPI generation.

The cache is only ever reused when *every* input that can affect the generated
contract is unchanged: tool version, app target, configuration, baseline,
dependency versions, and the content fingerprint of the project's sources.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Configuration
from .errors import CacheError
from .fs import ensure_dir
from .source import _SKIP_DIRS

_MAX_DEPTH = 1


@dataclass(frozen=True, slots=True)
class CacheKey:
    value: str

    @classmethod
    def for_run(
        cls,
        *,
        tool_version: str,
        app: str,
        config: Configuration,
        baseline_digest: str,
        source_roots: Sequence[Path],
        root: Path,
    ) -> CacheKey:
        """Factory: a stable fingerprint of every input that affects the contract."""
        material = json.dumps(
            {
                "tool": tool_version,
                "app": app,
                "config": config.model_dump(mode="json", exclude={"project_root"}),
                "baseline": baseline_digest,
                "deps": _dependency_versions(),
                "sources": _source_fingerprint(source_roots, root),
            },
            sort_keys=True,
        )
        return cls(hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest())


class Cache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def default(cls) -> Cache:
        import tempfile

        return cls(Path(tempfile.gettempdir()) / "contractwatch" / "cache")

    def load(self, key: CacheKey) -> dict[str, Any] | None:
        path = self.directory / f"{key.value}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def store(self, key: CacheKey, payload: dict[str, Any]) -> None:
        try:
            ensure_dir(self.directory)
            (self.directory / f"{key.value}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except OSError as exc:
            raise CacheError(f"Could not write cache: {exc}") from exc


def _dependency_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in ("pydantic", "fastapi"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = ""
    return versions


def _source_fingerprint(roots: Sequence[Path], root: Path) -> list[str]:
    entries: list[str] = []
    seen: set[Path] = set()
    for base in roots:
        for path in _iter_python(base):
            if path in seen:
                continue
            seen.add(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            entries.append(f"{rel}:{stat.st_size}:{stat.st_mtime_ns}")
    entries.sort()
    return entries


def _iter_python(base: Path) -> Iterator[Path]:
    if not base.is_dir():
        return
    for child in sorted(base.iterdir()):
        if child.name in _SKIP_DIRS or (child.name.startswith(".") and child.is_dir()):
            continue
        if child.is_dir():
            yield from _iter_python(child)
        elif child.suffix == ".py":
            yield child


def digest_file(path: Path) -> str:
    try:
        return hashlib.blake2b(path.read_bytes(), digest_size=16).hexdigest()
    except OSError:
        return ""


__all__ = ["Cache", "CacheKey", "digest_file"]
