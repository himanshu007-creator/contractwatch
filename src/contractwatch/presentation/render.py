"""Rich terminal output for results, errors, and reports."""

from __future__ import annotations

import json

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from ..diagnostics import AnalysisError, AnalysisResult, Diagnostic, Severity
from .capabilities import Capabilities
from .theme import BANNER, CHECK, CODE, MUTED, SEVERITY_GLYPH, SEVERITY_GLYPH_ASCII, SEVERITY_STYLE


class Renderer:
    """Console renderer. Uses Rich when colour is available, plain text otherwise."""

    def __init__(self, caps: Capabilities | None = None, console: Console | None = None) -> None:
        self.caps = caps or Capabilities.detect()
        self.console = console or Console(
            highlight=False, no_color=not self.caps.color, width=max(self.caps.width, 40)
        )

    def banner(self) -> None:
        self.console.print(Text(BANNER, style=CODE), style=MUTED)

    def result(self, result: AnalysisResult, *, app: str | None = None, baseline_ref: str | None = None) -> None:
        for error in result.errors:
            self.error(error)
        for finding in result.findings:
            self.finding(finding)
        if result.errors or result.blocking_findings:
            self.summary(result, app=app, baseline_ref=baseline_ref, ok=False)
        else:
            self.success(result, app=app, baseline_ref=baseline_ref)

    def success(self, result: AnalysisResult, *, app: str | None = None, baseline_ref: str | None = None) -> None:
        self.summary(result, app=app, baseline_ref=baseline_ref, ok=True)

    def summary(
        self,
        result: AnalysisResult,
        *,
        app: str | None,
        baseline_ref: str | None,
        ok: bool,
    ) -> None:
        lines = Text()
        verb = "No contract problems found" if ok else "Contract guard blocked this change"
        lines.append(verb + ". ")
        if app:
            lines.append(f"checked {app} ", style=MUTED)
        lines.append(
            f"({len(result.findings)} findings, "
            f"{len(result.blocking_findings)} blocking, "
            f"{len(result.errors)} errors, "
            f"{result.duration_ms}ms) ",
            style=MUTED,
        )
        if baseline_ref:
            lines.append(f"vs {baseline_ref}", style=MUTED)
        style = CHECK if ok else "bold red"
        self.console.print(Panel(lines, border_style="green" if ok else "red", style=style))

    def finding(self, finding: Diagnostic) -> None:
        label = "[" + finding.severity.value.upper() + "]"
        glyph = self._glyph(finding.severity)
        scope = finding.endpoint or finding.component
        header = Text()
        header.append(f"{glyph} {label} ", style=SEVERITY_STYLE[finding.severity])
        header.append(f"{finding.code} {finding.title}", style=CODE)
        if scope:
            header.append(f" — {scope}", style=SEVERITY_STYLE[finding.severity])
        self.console.print(header)
        self.console.print(f"  {finding.summary}", style=MUTED)
        for location in finding.locations:
            self.console.print(f"    at {location.render()}", style=MUTED)
        if finding.impact:
            self.console.print(f"  impact: {finding.impact}")
        if finding.remediation:
            block = Text()
            block.append("How to fix: ", style=CODE)
            block.append(finding.remediation.summary)
            self.console.print(f"  {block}")
            for step in finding.remediation.steps:
                self.console.print(f"    - {step}", style=MUTED)
            for command in finding.remediation.commands:
                self.console.print(f"    $ {command}", style=MUTED)
        self.console.print(Rule(style="dim"))

    def error(self, error: AnalysisError) -> None:
        self.console.print(Text(f"[ERROR] {error.code} {error.message}", style="bold red"))
        if error.detail:
            self.console.print(f"  hint: {error.detail}", style=MUTED)
        self.console.print(Rule(style="dim"))

    def result_json(self, result: AnalysisResult, *, app: str | None = None, baseline_ref: str | None = None) -> None:
        payload = {
            "exit_code": int(result.exit_code()),
            "summary": {
                "findings": len(result.findings),
                "blocking": len(result.blocking_findings),
                "warnings": len(result.warnings),
                "errors": len(result.errors),
                "routes_inspected": result.routes_inspected,
                "components_inspected": result.components_inspected,
                "duration_ms": result.duration_ms,
            },
            "app": app,
            "baseline": baseline_ref,
            "findings": [finding.to_dict() for finding in result.findings],
            "analysis_errors": [error.to_dict() for error in result.errors],
            "skipped": list(result.skipped),
        }
        self.console.print(json.dumps(payload, indent=2, sort_keys=True), markup=False, highlight=False, soft_wrap=True)

    def _glyph(self, severity: Severity) -> str:
        table = SEVERITY_GLYPH if self.caps.unicode else SEVERITY_GLYPH_ASCII
        return table[severity]


__all__ = ["Renderer"]
