"""Schema-name collision analysis.

A confirmed collision means *two different Python classes resolve to one
generated component name*. Repeated class names alone are never confirmed: they
are only a warning (ambiguous naming) or informational (not exposed), which is
the core safety property the PRD demands.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
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
from .source import ModelDeclaration, SourceIndex


@dataclass(slots=True)
class Exposure:
    """Runtime link between Python model classes, component names, and routes."""

    component_classes: dict[str, set[str]] = field(default_factory=dict)
    class_routes: dict[str, set[str]] = field(default_factory=dict)
    route_components: dict[str, set[str]] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Iterable[dict[str, object]]) -> Exposure:
        """Factory: build the map from bootstrap route records."""
        exposure = cls()
        for record in records:
            route = str(record.get("route", ""))
            response_models = _strings(record.get("response_models"))
            body_models = _strings(record.get("body_models"))
            response_components = _strings(record.get("response_components"))
            request_components = _strings(record.get("request_components"))
            for qualname in (*response_models, *body_models):
                exposure.class_routes.setdefault(qualname, set()).add(route)
            for qualname in response_models:
                for component in response_components:
                    exposure.link(component, qualname, route)
            for qualname in body_models:
                for component in request_components:
                    exposure.link(component, qualname, route)
        return exposure

    def link(self, component: str, qualname: str, route: str) -> None:
        self.component_classes.setdefault(component, set()).add(qualname)
        self.route_components.setdefault(route, set()).add(component)

    def components_of(self, qualname: str) -> set[str]:
        return {c for c, classes in self.component_classes.items() if qualname in classes}

    def routes_of(self, qualname: str) -> set[str]:
        return self.class_routes.get(qualname, set())


@dataclass(slots=True)
class CollisionContext:
    source: SourceIndex
    exposure: Exposure

    def locate(self, qualname: str) -> SourceLocation:
        declaration = self.source.by_qualname(qualname)
        route = self.exposure.routes_of(qualname)
        route_name = ", ".join(sorted(route)) if route else None
        if declaration is not None:
            return SourceLocation(
                declaration.file, declaration.line, declaration.module, route_name
            )
        module = qualname.rsplit(".", 1)[0] if "." in qualname else None
        return SourceLocation("<generated>", None, module, route_name)


class CollisionCheck(ABC):
    code: ClassVar[str]
    title: ClassVar[str]
    severity: ClassVar[Severity]
    category: ClassVar[str] = "schema"
    description: ClassVar[str] = ""
    spec: ClassVar[CodeSpec]
    _registry: ClassVar[list[type[CollisionCheck]]] = []

    @classmethod
    def registry(cls) -> tuple[type[CollisionCheck], ...]:
        return tuple(cls._registry)

    @abstractmethod
    def run(self, ctx: CollisionContext) -> list[Diagnostic]: ...

    def emit(
        self,
        summary: str,
        *,
        impact: str,
        remediation: Remediation,
        confidence: Confidence = Confidence.CONFIRMED,
        component: str | None = None,
        locations: tuple[SourceLocation, ...] = (),
    ) -> Diagnostic:
        return Diagnostic.create(
            self.spec,
            summary,
            confidence=confidence,
            component=component,
            locations=locations,
            impact=impact,
            remediation=remediation,
        )


def check(cls: type[CollisionCheck]) -> type[CollisionCheck]:
    spec = DiagnosticCatalog.register(
        CodeSpec(cls.code, cls.title, cls.severity, cls.category, cls.description)
    )
    cls.spec = spec
    CollisionCheck._registry.append(cls)
    return cls


@check
class ConfirmedComponentCollision(CollisionCheck):
    code = "CW201"
    title = "Confirmed schema-name collision"
    severity = Severity.ERROR
    description = "Two different Python models resolve to one generated OpenAPI component name."

    def run(self, ctx: CollisionContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        for component, classes in sorted(ctx.exposure.component_classes.items()):
            if len(classes) < 2:
                continue
            qualnames = sorted(classes)
            locations = tuple(ctx.locate(q) for q in qualnames)
            models = ", ".join(qualnames)
            files = tuple(dict.fromkeys(loc.file for loc in locations))
            findings.append(
                self.emit(
                    f"Component '{component}' is produced by {len(qualnames)} different models.",
                    component=component,
                    locations=locations,
                    impact=(
                        f"Models {models} resolve to the same generated component, so the "
                        "served contract is ambiguous or silently overwritten."
                    ),
                    remediation=Remediation(
                        summary=f"Give each model its own name so '{component}' is unique.",
                        steps=[
                            "Rename each conflicting model to a distinct, descriptive name "
                            "(e.g. UserResponse, OrderResponse).",
                            "Update every import and usage of the renamed classes.",
                            "Re-run `contractwatch check` to confirm the collision is resolved.",
                        ],
                        files=files,
                        symbols=[component, *qualnames],
                        commands=["contractwatch check"],
                    ),
                )
            )
        return findings


@check
class AmbiguousRepeatedModel(CollisionCheck):
    code = "CW202"
    title = "Ambiguous repeated model name"
    severity = Severity.WARNING
    description = "The same model name is declared in multiple modules and is exposed by routes."

    def run(self, ctx: CollisionContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        for name, declarations in sorted(ctx.source.pydantic_names().items()):
            if len(declarations) < 2:
                continue
            exposed = [d for d in declarations if ctx.exposure.components_of(d.qualname)]
            if not exposed:
                continue
            components = {c for d in exposed for c in ctx.exposure.components_of(d.qualname)}
            if len(components) <= 1 and len(exposed) == len(declarations):
                continue  # a single shared component is CW201's job
            locations = tuple(_decl_location(ctx, d) for d in declarations)
            files = tuple(dict.fromkeys(loc.file for loc in locations))
            findings.append(
                self.emit(
                    f"Model name '{name}' is declared {len(declarations)} times across modules.",
                    confidence=Confidence.POTENTIAL,
                    locations=locations,
                    impact=(
                        "Generated component names depend on module paths; the contract may "
                        "change unexpectedly if declarations move or are merged."
                    ),
                    remediation=Remediation(
                        summary=f"Rename one or more of the '{name}' models to distinct names.",
                        steps=[
                            f"Review every declaration of '{name}' listed in locations.",
                            "Rename the models so each has a unique, descriptive name.",
                            "Regenerate clients if component names changed.",
                        ],
                        files=files,
                        symbols=[name, *[d.qualname for d in declarations]],
                    ),
                )
            )
        return findings


@check
class UnexposedRepeatedModel(CollisionCheck):
    code = "CW203"
    title = "Repeated model name not exposed through FastAPI"
    severity = Severity.INFO
    description = "The same model name is declared more than once but none is served by a route."

    def run(self, ctx: CollisionContext) -> list[Diagnostic]:
        findings: list[Diagnostic] = []
        for name, declarations in sorted(ctx.source.pydantic_names().items()):
            if len(declarations) < 2:
                continue
            if any(ctx.exposure.components_of(d.qualname) for d in declarations):
                continue
            locations = tuple(_decl_location(ctx, d) for d in declarations)
            files = tuple(dict.fromkeys(loc.file for loc in locations))
            findings.append(
                self.emit(
                    f"Model name '{name}' is declared {len(declarations)} times but is not exposed.",
                    locations=locations,
                    impact="No OpenAPI impact today; may become a collision if the models are used.",
                    remediation=Remediation(
                        summary=f"Consider distinct names for the '{name}' models.",
                        steps=[
                            f"Review the declarations of '{name}' in the listed files.",
                            "Rename any model that may be exposed by a route in the future.",
                        ],
                        files=files,
                        symbols=[name, *[d.qualname for d in declarations]],
                    ),
                )
            )
        return findings


def _decl_location(ctx: CollisionContext, declaration: ModelDeclaration) -> SourceLocation:
    route = ctx.exposure.routes_of(declaration.qualname)
    route_name = ", ".join(sorted(route)) if route else None
    return SourceLocation(declaration.file, declaration.line, declaration.module, route_name)


def _strings(value: object) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    return [item for item in value if isinstance(item, str)]


def analyze_collisions(ctx: CollisionContext) -> list[Diagnostic]:
    findings: list[Diagnostic] = []
    for check_cls in CollisionCheck.registry():
        findings.extend(check_cls().run(ctx))
    return findings


__all__ = [
    "AmbiguousRepeatedModel",
    "CollisionCheck",
    "CollisionContext",
    "ConfirmedComponentCollision",
    "Exposure",
    "UnexposedRepeatedModel",
    "analyze_collisions",
    "check",
]
