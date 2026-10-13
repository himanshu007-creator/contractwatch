"""Persistence of run reports under a private, user-scoped directory."""

from __future__ import annotations

import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ..config import Retention
from ..errors import ReportError
from ..fs import atomic_write_json, atomic_write_text, ensure_dir
from .report import Report

_LATEST = "latest.json"
_HOME_ENV = "CONTRACTWATCH_HOME"


def default_home() -> Path:
    override = os.environ.get(_HOME_ENV)
    if override:
        return Path(override)
    return Path.home() / ".contractwatch"


class ReportStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def default(cls) -> ReportStore:
        return cls(default_home() / "reports")

    @classmethod
    def at(cls, home: Path) -> ReportStore:
        return cls(home / "reports")

    def save(self, report: Report, *, retention: Retention | None = None) -> Path:
        ensure_dir(self.directory)
        stem = _timestamp(report.created)
        json_path = self.directory / f"{stem}.json"
        text_path = self.directory / f"{stem}.txt"
        try:
            atomic_write_json(json_path, report.to_dict())
            atomic_write_text(text_path, report.to_text())
            atomic_write_json(self.directory / _LATEST, report.to_dict())
        except ReportError:
            raise
        except OSError as exc:
            raise ReportError(f"Could not persist report: {exc}") from exc
        self._prune(retention or Retention())
        return json_path

    def latest(self) -> Report | None:
        path = self.directory / _LATEST
        if not path.exists():
            return None
        from ..fs import read_json

        data = read_json(path)
        return _report_from_dict(data)

    def latest_text_path(self) -> Path | None:
        entries = self.history()
        if not entries:
            return None
        candidate = entries[0].with_suffix(".txt")
        return candidate if candidate.is_file() else None

    def history(self) -> list[Path]:
        if not self.directory.is_dir():
            return []
        return sorted(
            (p for p in self.directory.glob("*.json") if p.name != _LATEST),
            reverse=True,
        )

    def _prune(self, retention: Retention) -> None:
        entries = self.history()
        cutoff = datetime.now(UTC) - timedelta(days=retention.days)
        for index, path in enumerate(entries):
            if index < retention.keep:
                continue
            if path.is_file() and _older_than(path, cutoff):
                with suppress(OSError):
                    path.unlink()
                with suppress(OSError):
                    path.with_suffix(".txt").unlink(missing_ok=True)


def _timestamp(created: str) -> str:
    return created.replace(":", "").replace("-", "").replace("+00:00", "Z").replace(".", "")


def _older_than(path: Path, cutoff: datetime) -> bool:
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return False
    return modified < cutoff


def _report_from_dict(data: dict[str, Any]) -> Report:
    from ..diagnostics import (
        AnalysisError,
        AnalysisResult,
        Confidence,
        Diagnostic,
        Remediation,
        Severity,
        SourceLocation,
    )

    def _remediation(raw: dict[str, Any] | None) -> Remediation | None:
        if not raw:
            return None
        return Remediation(
            summary=raw.get("summary", ""),
            steps=tuple(raw.get("steps", ())),
            files=tuple(raw.get("files", ())),
            symbols=tuple(raw.get("symbols", ())),
            commands=tuple(raw.get("commands", ())),
            example=raw.get("example"),
        )

    findings = [
        Diagnostic(
            code=item["code"],
            severity=Severity(item["severity"]),
            confidence=Confidence(item["confidence"]),
            title=item["title"],
            summary=item["summary"],
            endpoint=item.get("endpoint"),
            component=item.get("component"),
            locations=tuple(
                SourceLocation(
                    file=loc["file"],
                    line=loc.get("line"),
                    module=loc.get("module"),
                    route=loc.get("route"),
                )
                for loc in item.get("locations", ())
            ),
            impact=item.get("impact"),
            remediation=_remediation(item.get("remediation")),
            before=item.get("before"),
            after=item.get("after"),
        )
        for item in data.get("findings", ())
    ]
    errors = [
        AnalysisError(
            code=item["code"],
            message=item["message"],
            detail=item.get("detail"),
            remediation=_remediation(item.get("remediation")),
        )
        for item in data.get("analysis_errors", ())
    ]
    summary = data.get("summary", {})
    result = AnalysisResult(
        findings=findings,
        errors=errors,
        routes_inspected=summary.get("routes_inspected", 0),
        components_inspected=summary.get("components_inspected", 0),
        duration_ms=summary.get("duration_ms", 0),
        baseline_ref=data.get("baseline_ref"),
        skipped=list(data.get("skipped", [])),
        threshold=Severity(data.get("threshold", Severity.ERROR.value)),
    )
    git = data.get("git", {})
    return Report(
        result=result,
        app=data.get("app"),
        project_root=data.get("project_root", ""),
        git_commit=git.get("commit"),
        git_branch=git.get("branch"),
        tool_version=data.get("tool_version", ""),
        created=data.get("created", ""),
    )


__all__ = ["ReportStore", "default_home"]
