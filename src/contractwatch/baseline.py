"""Baseline management: the accepted contract that future runs compare against.

A baseline is *never* overwritten implicitly. Only ``contractwatch baseline
update`` (or ``init``) creates or replaces one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .contract import Contract
from .errors import BaselineError
from .fs import atomic_write_json

SCHEMA_VERSION = 1


@dataclass(slots=True)
class Baseline:
    openapi: dict[str, Any]
    app: str | None = None
    created: str = ""
    tool_version: str = __version__
    schema_version: int = SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> Baseline:
        import json

        if not path.exists():
            raise BaselineError(
                f"No baseline found at {path}.",
                hint="Create one with `contractwatch init` or `contractwatch baseline update`.",
                code="CW401",
            )
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BaselineError(
                f"Baseline at {path} is unreadable: {exc}.",
                hint="Regenerate it with `contractwatch baseline update`.",
                code="CW401",
            ) from exc
        if not isinstance(data, dict) or "openapi" not in data:
            raise BaselineError(
                f"Baseline at {path} is malformed (missing 'openapi').",
                hint="Regenerate it with `contractwatch baseline update`.",
                code="CW401",
            )
        return cls(
            openapi=data["openapi"],
            app=data.get("app"),
            created=data.get("created", ""),
            tool_version=data.get("tool_version", __version__),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )

    @classmethod
    def exists(cls, path: Path) -> bool:
        return path.exists()

    @classmethod
    def from_openapi(cls, openapi: dict[str, Any], *, app: str | None = None) -> Baseline:
        return cls(openapi=openapi, app=app, created=datetime.now(UTC).isoformat())

    def contract(self) -> Contract:
        return Contract.from_openapi(self.openapi)

    def save(self, path: Path) -> None:
        atomic_write_json(
            path,
            {
                "schema_version": self.schema_version,
                "tool_version": self.tool_version,
                "app": self.app,
                "created": self.created or datetime.now(UTC).isoformat(),
                "openapi": self.openapi,
            },
        )


__all__ = ["SCHEMA_VERSION", "Baseline"]
