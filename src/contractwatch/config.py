"""Project configuration: minimal defaults with explicit, validated overrides.

Read from ``contractwatch.toml`` when present, otherwise ``[tool.contractwatch]``
in ``pyproject.toml``. Never writes project files from here.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .diagnostics import Severity
from .errors import ConfigError, InvalidConfigurationError

CONFIG_FILENAME = "contractwatch.toml"
PYPROJECT_FILENAME = "pyproject.toml"
DEFAULT_BASELINE = ".contractwatch/baseline.json"


class Retention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: int = Field(default=20, ge=1)
    days: int = Field(default=7, ge=1)


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    app: str | None = None
    baseline: str = DEFAULT_BASELINE
    severity_threshold: Severity = Severity.ERROR
    max_findings: int = Field(default=10, ge=1)
    ignore: tuple[str, ...] = ()
    retention: Retention = Field(default_factory=Retention)
    cache: bool = True
    source_roots: tuple[str, ...] = ()
    project_root: Path = Field(default=Path("."), exclude=True)

    @classmethod
    def load(
        cls,
        project_root: Path,
        *,
        overrides: Mapping[str, Any] | None = None,
    ) -> Configuration:
        """Factory: load and validate configuration for ``project_root``."""
        data = _read_config(project_root)
        if overrides:
            data.update({k: v for k, v in overrides.items() if v is not None})
        try:
            return cls(project_root=project_root, **data)
        except ValidationError as exc:
            raise InvalidConfigurationError(_format_validation(exc)) from exc

    @property
    def baseline_path(self) -> Path:
        return (self.project_root / self.baseline).resolve()

    @property
    def source_root_paths(self) -> list[Path]:
        return [(self.project_root / root).resolve() for root in self.source_roots]

    def ignores(self, code: str) -> bool:
        return code in self.ignore


def _read_config(project_root: Path) -> dict[str, Any]:
    dedicated = project_root / CONFIG_FILENAME
    if dedicated.exists():
        return _parse_toml(dedicated, whole_file=True)
    pyproject = project_root / PYPROJECT_FILENAME
    if pyproject.exists():
        table = _parse_toml(pyproject, whole_file=False)
        return dict(table)
    return {}


def _parse_toml(path: Path, *, whole_file: bool) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            document = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {path.name}: {exc}", hint="Fix the TOML syntax.") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read {path.name}: {exc}") from exc
    if whole_file:
        table = document.get("tool", {}).get("contractwatch", document)
    else:
        table = document.get("tool", {}).get("contractwatch", {})
    return dict(table) if isinstance(table, Mapping) else {}


def _format_validation(exc: ValidationError) -> str:
    problems = "; ".join(
        f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}" for error in exc.errors()
    )
    return f"Invalid ContractWatch configuration: {problems}"


__all__ = ["CONFIG_FILENAME", "DEFAULT_BASELINE", "Configuration", "Retention"]
