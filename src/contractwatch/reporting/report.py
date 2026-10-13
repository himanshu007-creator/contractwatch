"""The persisted shape of a single run."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .. import __version__
from ..diagnostics import AnalysisResult, Diagnostic, ExitCode


@dataclass(slots=True)
class Report:
    result: AnalysisResult
    app: str | None = None
    project_root: str = ""
    git_commit: str | None = None
    git_branch: str | None = None
    tool_version: str = __version__
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: int = 1

    @classmethod
    def from_run(
        cls,
        result: AnalysisResult,
        *,
        app: str | None,
        project_root: str,
        git_commit: str | None = None,
        git_branch: str | None = None,
    ) -> Report:
        return cls(
            result=result,
            app=app,
            project_root=project_root,
            git_commit=git_commit,
            git_branch=git_branch,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool_version": self.tool_version,
            "created": self.created,
            "app": self.app,
            "project_root": self.project_root,
            "git": {"commit": self.git_commit, "branch": self.git_branch},
            "exit_code": int(self.result.exit_code()),
            "summary": {
                "findings": len(self.result.findings),
                "blocking": len(self.result.blocking_findings),
                "warnings": len(self.result.warnings),
                "errors": len(self.result.errors),
                "routes_inspected": self.result.routes_inspected,
                "components_inspected": self.result.components_inspected,
                "duration_ms": self.result.duration_ms,
            },
            "baseline_ref": self.result.baseline_ref,
            "threshold": self.result.threshold.value,
            "findings": [finding.to_dict() for finding in self.result.findings],
            "analysis_errors": [error.to_dict() for error in self.result.errors],
            "skipped": list(self.result.skipped),
        }

    def to_text(self) -> str:
        """Human-readable rendering carrying the same essence as :meth:`to_dict`."""
        result = self.result
        lines = [
            f"ContractWatch {self.tool_version} report",
            f"Generated: {self.created}",
            f"Project:   {self.project_root}",
            f"App:       {self.app or 'unknown'}",
        ]
        if self.git_commit:
            suffix = f" ({self.git_branch})" if self.git_branch else ""
            lines.append(f"Git:       {self.git_commit}{suffix}")
        status = ExitCode(result.exit_code()).name
        lines.append(f"Baseline:  {result.baseline_ref or 'none'}")
        lines.append(f"Result:    {status} (exit {int(result.exit_code())})")
        lines.append("")
        lines.append(
            f"Summary: {len(result.blocking_findings)} blocking, "
            f"{len(result.warnings)} warning(s), {len(result.errors)} error(s) · "
            f"{result.routes_inspected} routes · {result.components_inspected} schemas · "
            f"{result.duration_ms}ms"
        )

        lines.append("")
        lines.append("Findings")
        lines.append("--------")
        if result.findings:
            for index, finding in enumerate(result.findings, 1):
                lines.extend(_finding_lines(index, finding))
        else:
            lines.append("(none)")

        lines.append("")
        lines.append("Analysis errors")
        lines.append("---------------")
        if result.errors:
            for error in result.errors:
                lines.append(f"[{error.code}] {error.message}")
                if error.detail:
                    lines.append(f"  {error.detail}")
                if error.remediation:
                    lines.append(f"  Fix: {error.remediation.summary}")
        else:
            lines.append("(none)")

        if result.skipped:
            lines.append("")
            lines.append("Skipped")
            lines.append("-------")
            lines.extend(f"- {item}" for item in result.skipped)

        return "\n".join(lines) + "\n"


def _finding_lines(index: int, finding: Diagnostic) -> list[str]:
    scope = finding.endpoint or finding.component or ""
    header = f"[{finding.severity.value.upper()}] {finding.code} {finding.title}"
    lines = [f"{index}. {header}{f'  ({scope})' if scope else ''}"]
    lines.append(f"   {finding.summary}")
    if finding.impact:
        lines.append(f"   Impact: {finding.impact}")
    for location in finding.locations:
        lines.append(f"   Location: {location.render()}")
    if finding.before or finding.after:
        lines.append(f"   Before: {finding.before or '-'}")
        lines.append(f"   After:  {finding.after or '-'}")
    if finding.remediation:
        remedy = finding.remediation
        lines.append(f"   Fix: {remedy.summary}")
        lines.extend(f"     - {step}" for step in remedy.steps)
        if remedy.files:
            lines.append(f"     files: {', '.join(remedy.files)}")
        if remedy.symbols:
            lines.append(f"     symbols: {', '.join(remedy.symbols)}")
        lines.extend(f"     $ {command}" for command in remedy.commands)
        if remedy.example:
            lines.append(f"     example: {remedy.example}")
    return lines


__all__ = ["Report"]
