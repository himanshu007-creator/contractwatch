"""Diagnostics: stable codes, severity, confidence, source locations, remedies.

The analysis engine produces :class:`Diagnostic` values only. Rendering, exit
codes, reports, and the machine-readable JSON consumed by AI agents are all
derived from them, so the engine stays presentation-free.

Every diagnostic carries a :class:`Remediation` describing *exactly* what to
change and where, so the JSON report is sufficient to fix an issue without any
human translation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any, ClassVar


class ExitCode(IntEnum):
    """Process exit codes documented in the PRD."""

    OK = 0
    BLOCKED = 1
    ERROR = 2
    INTERRUPTED = 130


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def rank(self) -> int:
        return {"error": 3, "warning": 2, "info": 1}[self.value]


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    POTENTIAL = "potential"


@dataclass(frozen=True, slots=True)
class CodeSpec:
    code: str
    title: str
    severity: Severity
    category: str
    description: str = ""


class DiagnosticCatalog:
    """Registry mapping diagnostic codes to stable metadata.

    Rules self-register through the ``@rule`` / ``@check`` decorators, keeping
    each code's title, severity, and category in exactly one place.
    """

    _specs: ClassVar[dict[str, CodeSpec]] = {}

    @classmethod
    def register(cls, spec: CodeSpec) -> CodeSpec:
        existing = cls._specs.get(spec.code)
        if existing is not None and existing != spec:
            raise ValueError(f"conflicting registration for {spec.code!r}")
        cls._specs[spec.code] = spec
        return spec

    @classmethod
    def get(cls, code: str) -> CodeSpec | None:
        return cls._specs.get(code)

    @classmethod
    def all(cls) -> dict[str, CodeSpec]:
        return dict(cls._specs)


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """Where a finding originates: a source file, a model, and/or a route."""

    file: str
    line: int | None = None
    module: str | None = None
    route: str | None = None

    def render(self) -> str:
        location = f"{self.file}:{self.line}" if self.line else self.file
        if self.module:
            location = f"{location} ({self.module})"
        return location

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"file": self.file}
        if self.line is not None:
            data["line"] = self.line
        if self.module:
            data["module"] = self.module
        if self.route:
            data["route"] = self.route
        return data


@dataclass(frozen=True, slots=True)
class Remediation:
    """A concrete, ordered recipe for fixing a finding.

    ``steps`` are imperative actions, ``files``/``symbols`` are the exact edit
    targets, ``commands`` are copy-pasteable CLI commands, and ``example`` is a
    short snippet. This structure is what makes a finding agent-fixable.
    """

    summary: str
    steps: Sequence[str] = ()
    files: Sequence[str] = ()
    symbols: Sequence[str] = ()
    commands: Sequence[str] = ()
    example: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "steps": list(self.steps),
            "files": list(self.files),
            "symbols": list(self.symbols),
            "commands": list(self.commands),
            "example": self.example,
        }


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """A single, actionable finding."""

    code: str
    severity: Severity
    confidence: Confidence
    title: str
    summary: str
    endpoint: str | None = None
    component: str | None = None
    locations: tuple[SourceLocation, ...] = ()
    impact: str | None = None
    remediation: Remediation | None = None
    before: str | None = None
    after: str | None = None

    @property
    def suggestion(self) -> str | None:
        return self.remediation.summary if self.remediation else None

    @classmethod
    def create(
        cls,
        spec: CodeSpec,
        summary: str,
        *,
        confidence: Confidence = Confidence.CONFIRMED,
        endpoint: str | None = None,
        component: str | None = None,
        locations: tuple[SourceLocation, ...] = (),
        impact: str | None = None,
        remediation: Remediation | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> Diagnostic:
        return cls(
            code=spec.code,
            severity=spec.severity,
            confidence=confidence,
            title=spec.title,
            summary=summary,
            endpoint=endpoint,
            component=component,
            locations=locations,
            impact=impact,
            remediation=remediation,
            before=before,
            after=after,
        )

    @property
    def blocking(self) -> bool:
        """Only confirmed errors block a commit; warnings and info never do."""
        return self.severity is Severity.ERROR and self.confidence is Confidence.CONFIRMED

    def to_dict(self) -> dict[str, Any]:
        spec = DiagnosticCatalog.get(self.code)
        return {
            "code": self.code,
            "title": self.title,
            "description": spec.description if spec else None,
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "blocking": self.blocking,
            "summary": self.summary,
            "endpoint": self.endpoint,
            "component": self.component,
            "locations": [loc.to_dict() for loc in self.locations],
            "impact": self.impact,
            "remediation": self.remediation.to_dict() if self.remediation else None,
            "before": self.before,
            "after": self.after,
        }

    def one_line(self) -> str:
        scope = self.endpoint or self.component or ""
        return f"{self.code} {self.title}{f' [{scope}]' if scope else ''}: {self.summary}"


@dataclass(frozen=True, slots=True)
class AnalysisError:
    """A check that could not run. Distinct from a finding; forces exit code 2."""

    code: str
    message: str
    detail: str | None = None
    remediation: Remediation | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "detail": self.detail,
            "remediation": self.remediation.to_dict() if self.remediation else None,
        }


@dataclass(slots=True)
class AnalysisResult:
    """Everything the CLI, reports, and exit-code logic need."""

    findings: list[Diagnostic] = field(default_factory=list)
    errors: list[AnalysisError] = field(default_factory=list)
    routes_inspected: int = 0
    components_inspected: int = 0
    duration_ms: int = 0
    baseline_ref: str | None = None
    skipped: list[str] = field(default_factory=list)
    threshold: Severity = Severity.ERROR

    @property
    def blocking_findings(self) -> list[Diagnostic]:
        return [
            f
            for f in self.findings
            if f.confidence is Confidence.CONFIRMED and f.severity.rank >= self.threshold.rank
        ]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [f for f in self.findings if f.severity is Severity.WARNING]

    def exit_code(self) -> ExitCode:
        if self.errors:
            return ExitCode.ERROR
        if self.blocking_findings:
            return ExitCode.BLOCKED
        return ExitCode.OK


__all__ = [
    "AnalysisError",
    "AnalysisResult",
    "CodeSpec",
    "Confidence",
    "Diagnostic",
    "DiagnosticCatalog",
    "ExitCode",
    "Remediation",
    "Severity",
    "SourceLocation",
]
