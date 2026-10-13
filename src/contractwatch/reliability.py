"""Reliability checks: common backend pitfalls discovered via OpenAPI and AST."""

from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from .diagnostics import (
    CodeSpec,
    Confidence,
    Diagnostic,
    DiagnosticCatalog,
    Remediation,
    Severity,
    SourceLocation,
)
from .discovery import ProjectLayout
from .source import SourceIndex, _iter_python_files, _module_name, _relative  # reuse helpers

BLOCKING_CALLS = {
    "time.sleep",
    "requests.get",
    "requests.post",
    "requests.put",
    "requests.patch",
    "requests.delete",
    "requests.request",
    "requests.Session",
    "urllib.request.urlopen",
    "http.client.HTTPConnection",
}


class ReliabilityCheck:
    code: ClassVar[str]
    title: ClassVar[str]
    severity: ClassVar[Severity]
    category: ClassVar[str] = "reliability"
    description: ClassVar[str] = ""
    spec: ClassVar[CodeSpec]
    _registry: ClassVar[list[type[ReliabilityCheck]]] = []

    @classmethod
    def registry(cls) -> tuple[type[ReliabilityCheck], ...]:
        return tuple(cls._registry)

    def run(self, ctx: ReliabilityContext) -> list[Diagnostic]:
        raise NotImplementedError

    def emit(
        self,
        summary: str,
        *,
        locations: tuple[SourceLocation, ...] = (),
        impact: str | None = None,
        remediation: Remediation | None = None,
        confidence: Confidence = Confidence.CONFIRMED,
    ) -> Diagnostic:
        return Diagnostic.create(
            self.spec,
            summary,
            confidence=confidence,
            locations=locations,
            impact=impact,
            remediation=remediation,
        )


def register_check(cls: type[ReliabilityCheck]) -> type[ReliabilityCheck]:
    spec = DiagnosticCatalog.register(
        CodeSpec(cls.code, cls.title, cls.severity, cls.category, cls.description)
    )
    cls.spec = spec
    ReliabilityCheck._registry.append(cls)
    return cls


@dataclass(slots=True)
class ReliabilityContext:
    layout: ProjectLayout
    index: SourceIndex
    openapi: dict[str, object] = field(default_factory=dict)

    @property
    def root(self) -> Path:
        return self.layout.root


@register_check
class BlockingInAsync(ReliabilityCheck):
    code = "RL101"
    title = "Blocking call in async endpoint"
    severity = Severity.WARNING
    description = "Synchronous blocking I/O inside an async def can block the event loop."

    def run(self, ctx: ReliabilityContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        for path in _iter_python_files(ctx.root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except Exception:
                continue
            rel = _relative(path, ctx.root)
            module = _module_name(path, ctx.root)
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue  # only async
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call):
                        name = self._call_name(inner.func)
                        if name in BLOCKING_CALLS:
                            loc = SourceLocation(rel, inner.lineno, module)
                            findings.append(
                                self.emit(
                                    f"{name} is a blocking call inside async function '{node.name}'",
                                    locations=(loc,),
                                    impact="May block the event loop under load, increasing latency.",
                                    remediation=Remediation(
                                        summary="Replace with an async equivalent or run in a threadpool.",
                                        steps=[
                                            "Use httpx.AsyncClient with timeouts instead of requests.",
                                            "Replace time.sleep with asyncio.sleep or run_sync.",
                                            "Consider using anyio.to_thread.run_sync for CPU/IO blocking work.",
                                        ],
                                        files=(rel,),
                                        symbols=(node.name, name),
                                        commands=["pytest -q"],
                                    ),
                                )
                            )
        return findings

    def _call_name(self, func: ast.expr) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts: list[str] = []
            node: ast.expr = func
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            if isinstance(node, ast.Name):
                parts.append(node.id)
            parts.reverse()
            return ".".join(parts)
        return ""


@register_check
class MissingTimeouts(ReliabilityCheck):
    code = "RL102"
    title = "HTTP client call missing explicit timeout"
    severity = Severity.WARNING
    description = "Outbound HTTP calls without timeouts can hang requests indefinitely."

    def run(self, ctx: ReliabilityContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        for path in _iter_python_files(ctx.root):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except Exception:
                continue
            rel = _relative(path, ctx.root)
            module = _module_name(path, ctx.root)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _blocking_call_name_static(node.func)
                    if name in ("httpx.AsyncClient", "httpx.Client", "httpx.request", "requests.get", "requests.post", "requests.Session.request") and not self._has_timeout(node):
                            loc = SourceLocation(rel, node.lineno, module)
                            findings.append(
                                self.emit(
                                    f"{name} called without explicit timeout",
                                    locations=(loc,),
                                    impact="Network calls may hang indefinitely, reducing resilience.",
                                    remediation=Remediation(
                                        summary="Pass an explicit timeout to all outbound HTTP calls.",
                                        steps=[
                                            "Use timeout=httpx.Timeout(...) or a numeric timeout.",
                                            "Set sensible connect/read/write timeouts.",
                                        ],
                                        files=(rel,),
                                        symbols=(name,),
                                    ),
                                )
                            )
        return findings

    def _has_timeout(self, call: ast.Call) -> bool:
        return any(kw.arg == "timeout" for kw in call.keywords)


@register_check
class DuplicateRoutes(ReliabilityCheck):
    code = "RL103"
    title = "Duplicate route/method registration"
    severity = Severity.WARNING
    description = "Same HTTP method and path registered more than once."

    def run(self, ctx: ReliabilityContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        paths = ctx.openapi.get("paths") if isinstance(ctx.openapi, dict) else None
        if not isinstance(paths, dict):
            return findings
        for path, ops in paths.items():
            if not isinstance(ops, dict):
                continue
            counter: Counter[str] = Counter()
            for method in ("get", "post", "put", "delete", "patch", "options", "head", "trace"):
                if method in ops:
                    val = ops[method]
                    if isinstance(val, list):
                        counter[method] += len(val)
                    elif isinstance(val, dict):
                        counter[method] += 1
            for method, count in counter.items():
                if count > 1:
                    findings.append(
                        self.emit(
                            f"Route {method.upper()} {path} is registered {count} times",
                            impact="FastAPI will override or behave unexpectedly; this is often a bug.",
                            remediation=Remediation(
                                summary="Remove duplicate route registrations.",
                                steps=["Check for decorator duplication or include routers twice."],
                                commands=["contractwatch check"],
                            ),
                            confidence=Confidence.POTENTIAL,
                        )
                    )
        return findings


@register_check
class ConflictingOperationIds(ReliabilityCheck):
    code = "RL104"
    title = "Conflicting operationId values"
    severity = Severity.WARNING
    description = "Multiple operations share the same operationId."

    def run(self, ctx: ReliabilityContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        opids: dict[str, list[tuple[str, str]]] = defaultdict(list)
        paths = ctx.openapi.get("paths") if isinstance(ctx.openapi, dict) else None
        if not isinstance(paths, dict):
            return findings
        for path, ops in paths.items():
            if not isinstance(ops, dict):
                continue
            for method, spec in ops.items():
                if isinstance(spec, dict) and isinstance(spec.get("operationId"), str):
                    opids[spec["operationId"]].append((method, path))
        for opid, entries in opids.items():
            if len(entries) > 1:
                locations = tuple(SourceLocation("<openapi>", None, route=f"{m.upper()} {p}") for m, p in entries)
                findings.append(
                    self.emit(
                        f"operationId '{opid}' is used by {len(entries)} operations",
                        locations=locations,
                        impact="Client code generation and tooling may be ambiguous.",
                        remediation=Remediation(
                            summary="Ensure each operation has a unique operationId.",
                            steps=["Set explicit, unique operationId values on each endpoint."],
                        ),
                    )
                )
        return findings


# Attach static helper for classmethods
def _blocking_call_name_static(func: ast.expr) -> str:
    try:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            parts = []
            cur = func
            while isinstance(cur, ast.Attribute):
                parts.append(cur.attr)
                if not hasattr(cur, "value"):
                    break
                cur = cur.value  # type: ignore[assignment]
            if isinstance(cur, ast.Name):
                parts.append(cur.id)
            parts.reverse()
            return ".".join(parts)
    except Exception:
        return ""
    return ""



def run_reliability(ctx: ReliabilityContext) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for check_cls in ReliabilityCheck.registry():
        findings.extend(check_cls().run(ctx))
    return findings


__all__ = [
    "BlockingInAsync",
    "ConflictingOperationIds",
    "DuplicateRoutes",
    "MissingTimeouts",
    "ReliabilityCheck",
    "ReliabilityContext",
    "register_check",
    "run_reliability",
]
