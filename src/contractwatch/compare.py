"""Contract comparison.

A single structural ``diff`` walks baseline and proposed contracts and yields
typed :class:`ChangeEvent` values. Each :class:`Rule` subscribes to the events it
cares about, so checks stay small, independent, and individually testable.

Every rule emits a :class:`Remediation` with ordered, copy-pasteable fix steps so
the JSON report can be actioned by a human or an AI agent without interpretation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

from .contract import Contract, Field, Route, Schema
from .diagnostics import (
    CodeSpec,
    Confidence,
    Diagnostic,
    DiagnosticCatalog,
    Remediation,
    Severity,
)

_MAX_DEPTH = 16
_REQUEST = "request"
_RESPONSE = "response"
_ACCEPT = "contractwatch baseline update"


class Change(Enum):
    ENDPOINT_REMOVED = "endpoint_removed"
    ENDPOINT_ADDED = "endpoint_added"
    STATUS_REMOVED = "status_removed"
    FIELD_REMOVED = "field_removed"
    FIELD_ADDED = "field_added"
    REQUIRED_STRICTENED = "required_strictened"
    REQUIRED_RELAXED = "required_relaxed"
    TYPE_CHANGED = "type_changed"
    NULLABILITY_CHANGED = "nullability_changed"
    ENUM_CHANGED = "enum_changed"
    KIND_CHANGED = "kind_changed"
    REF_CHANGED = "ref_changed"


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    kind: Change
    direction: str
    pointer: str
    endpoint: str | None = None
    component: str | None = None
    before: object | None = None
    after: object | None = None


# --------------------------------------------------------------------------- #
# type compatibility
# --------------------------------------------------------------------------- #


def _kind(schema: Schema | None) -> str:
    if schema is None:
        return "any"
    if schema.type == "array":
        return "array"
    if schema.type == "union":
        return "union"
    if schema.is_object():
        return "object"
    return schema.type or "any"


def _type_only(schema: Schema | None) -> Schema | None:
    """Strip nullability/enum so structural compatibility ignores them."""
    if schema is None:
        return None
    return Schema(
        type=schema.type,
        fields=schema.fields,
        items=schema.items,
        variants=schema.variants,
        ref=schema.ref,
    )


def _accepts(lhs: Schema | None, rhs: Schema | None, depth: int = 0) -> bool:
    """True when every value valid under ``lhs`` is also valid under ``rhs``."""
    if rhs is None or lhs is None or depth > _MAX_DEPTH:
        return True
    if lhs.type == "union":
        return all(_accepts(v, rhs, depth + 1) for v in lhs.variants)
    if rhs.type == "union":
        return any(_accepts(lhs, v, depth + 1) for v in rhs.variants)
    if lhs.nullable and not rhs.nullable:
        return False
    if rhs.enum is not None and (lhs.enum is None or not set(lhs.enum) <= set(rhs.enum)):
        return False
    if _kind(lhs) == "array" or _kind(rhs) == "array":
        if _kind(lhs) != "array" or _kind(rhs) != "array":
            return False
        return _accepts(lhs.items, rhs.items, depth + 1)
    if _kind(lhs) == "object" or _kind(rhs) == "object":
        if _kind(lhs) != "object" or _kind(rhs) != "object":
            return False
        for name, field_ in lhs.fields.items():
            target = rhs.fields.get(name)
            if target is None or (not field_.required and target.required):
                return False
            if not _accepts(field_.schema, target.schema, depth + 1):
                return False
        return True
    if lhs.ref and rhs.ref:
        return lhs.ref == rhs.ref
    return (lhs.type or "any") == (rhs.type or "any")


def _type_breaks(direction: str, base: Schema, cur: Schema) -> bool:
    if direction == _REQUEST:
        return not _accepts(_type_only(base), _type_only(cur))
    return not _accepts(_type_only(cur), _type_only(base))


# --------------------------------------------------------------------------- #
# structural diff
# --------------------------------------------------------------------------- #


def diff_contracts(baseline: Contract, current: Contract) -> Iterator[ChangeEvent]:
    for key, route in baseline.routes.items():
        counterpart = current.routes.get(key)
        if counterpart is None:
            yield ChangeEvent(Change.ENDPOINT_REMOVED, _REQUEST, key, endpoint=key)
            continue
        yield from _diff_route(route, counterpart)
    for key in current.routes.keys() - baseline.routes.keys():
        yield ChangeEvent(Change.ENDPOINT_ADDED, _RESPONSE, key, endpoint=key)


def _diff_route(base: Route, cur: Route) -> Iterator[ChangeEvent]:
    for name, before in base.request_fields.items():
        after = cur.request_fields.get(name)
        if after is None:
            yield ChangeEvent(Change.FIELD_REMOVED, _REQUEST, name, endpoint=base.key, before=before)
            continue
        yield from _diff_field(_REQUEST, base.key, name, before, after)
    for name in cur.request_fields.keys() - base.request_fields.keys():
        yield ChangeEvent(
            Change.FIELD_ADDED, _REQUEST, name, endpoint=base.key, after=cur.request_fields[name]
        )
    for status, base_schema in base.responses.items():
        cur_schema = cur.responses.get(status)
        if cur_schema is None:
            yield ChangeEvent(Change.STATUS_REMOVED, _RESPONSE, status, endpoint=base.key)
            continue
        yield from _diff_schema(_RESPONSE, base.key, f"response {status}", base_schema, cur_schema)


def _diff_field(
    direction: str, endpoint: str, pointer: str, base: Field, cur: Field
) -> Iterator[ChangeEvent]:
    if base.required != cur.required:
        kind = Change.REQUIRED_STRICTENED if cur.required else Change.REQUIRED_RELAXED
        yield ChangeEvent(kind, direction, pointer, endpoint=endpoint)
    yield from _diff_schema(direction, endpoint, pointer, base.schema, cur.schema)


def _diff_schema(
    direction: str, endpoint: str, pointer: str, base: Schema, cur: Schema, depth: int = 0
) -> Iterator[ChangeEvent]:
    if depth > _MAX_DEPTH:
        return
    base_kind, cur_kind = _kind(base), _kind(cur)
    if base_kind != cur_kind and "any" not in (base_kind, cur_kind):
        yield ChangeEvent(
            Change.KIND_CHANGED, direction, pointer, endpoint=endpoint, before=base, after=cur
        )
        return
    if base_kind == "object":
        for name, before in base.fields.items():
            after = cur.fields.get(name)
            if after is None:
                yield ChangeEvent(
                    Change.FIELD_REMOVED, direction, f"{pointer}.{name}",
                    endpoint=endpoint, component=base.ref, before=before,
                )
                continue
            yield from _diff_field(direction, endpoint, f"{pointer}.{name}", before, after)
        for name in cur.fields.keys() - base.fields.keys():
            yield ChangeEvent(
                Change.FIELD_ADDED, direction, f"{pointer}.{name}",
                endpoint=endpoint, component=cur.ref, after=cur.fields[name],
            )
        if base.ref and cur.ref and base.ref != cur.ref:
            yield ChangeEvent(
                Change.REF_CHANGED, direction, pointer, endpoint=endpoint,
                component=cur.ref, before=base.ref, after=cur.ref,
            )
        return
    if base_kind == "array":
        if base.items is not None and cur.items is not None:
            yield from _diff_schema(
                direction, endpoint, f"{pointer}[]", base.items, cur.items, depth + 1
            )
        return
    if base.ref and cur.ref and base.ref != cur.ref:
        yield ChangeEvent(
            Change.REF_CHANGED, direction, pointer, endpoint=endpoint,
            component=cur.ref, before=base.ref, after=cur.ref,
        )
    if base.enum != cur.enum:
        yield ChangeEvent(
            Change.ENUM_CHANGED, direction, pointer, endpoint=endpoint, before=base, after=cur
        )
    if base.nullable != cur.nullable:
        yield ChangeEvent(
            Change.NULLABILITY_CHANGED, direction, pointer, endpoint=endpoint, before=base, after=cur
        )
    if _type_breaks(direction, base, cur):
        yield ChangeEvent(
            Change.TYPE_CHANGED, direction, pointer, endpoint=endpoint, before=base, after=cur
        )


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


class Rule(ABC):
    """Base class for a comparison check. Subclasses self-register via ``@rule``."""

    code: ClassVar[str]
    title: ClassVar[str]
    severity: ClassVar[Severity]
    category: ClassVar[str] = "contract"
    description: ClassVar[str] = ""
    events: ClassVar[frozenset[Change]]
    spec: ClassVar[CodeSpec]
    _registry: ClassVar[list[type[Rule]]] = []

    @classmethod
    def registry(cls) -> tuple[type[Rule], ...]:
        return tuple(cls._registry)

    @abstractmethod
    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        """Return a diagnostic, or ``None`` when the change is compatible."""

    def emit(
        self,
        event: ChangeEvent,
        summary: str,
        *,
        impact: str,
        remediation: Remediation,
        confidence: Confidence = Confidence.CONFIRMED,
        before: str | None = None,
        after: str | None = None,
    ) -> Diagnostic:
        return Diagnostic.create(
            self.spec,
            summary,
            confidence=confidence,
            endpoint=event.endpoint,
            component=event.component,
            impact=impact,
            remediation=remediation,
            before=before,
            after=after,
        )


def rule(cls: type[Rule]) -> type[Rule]:
    """Class decorator: register a rule and its diagnostic code."""
    spec = DiagnosticCatalog.register(
        CodeSpec(cls.code, cls.title, cls.severity, cls.category, cls.description)
    )
    cls.spec = spec
    Rule._registry.append(cls)
    return cls


@rule
class RemovedEndpoint(Rule):
    code = "CW101"
    title = "Removed endpoint"
    severity = Severity.ERROR
    description = "An endpoint present in the accepted contract is no longer exposed."
    events = frozenset({Change.ENDPOINT_REMOVED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic:
        endpoint = event.endpoint or event.pointer
        return self.emit(
            event,
            f"Endpoint {endpoint} is no longer exposed.",
            impact="Existing clients calling this route will receive 404 responses.",
            remediation=Remediation(
                summary=f"Restore {endpoint}, or accept the removal explicitly.",
                steps=[
                    f"Locate the FastAPI route that previously handled {endpoint}.",
                    "Re-register the route, or move it to a path/version you intend to keep.",
                    f"If the removal is intentional, run: {_ACCEPT}",
                ],
                symbols=[endpoint],
                commands=[_ACCEPT],
            ),
        )


@rule
class RemovedResponseField(Rule):
    code = "CW102"
    title = "Removed response field"
    severity = Severity.ERROR
    description = "A field that the accepted contract returned is no longer returned."
    events = frozenset({Change.FIELD_REMOVED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        if event.direction != _RESPONSE:
            return None
        field_ = event.before if isinstance(event.before, Field) else None
        name = field_.name if field_ else event.pointer.rsplit(".", 1)[-1]
        return self.emit(
            event,
            f"Response field '{name}' was removed.",
            impact=f"Existing clients may still read '{name}' from the response.",
            remediation=Remediation(
                summary=f"Add '{name}' back to the response model, or accept the break.",
                steps=[
                    f"Find the response model used by {event.endpoint}.",
                    f"Re-add the '{name}' attribute with its original type.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
                example=f"{name}: <original-type>",
            ),
        )


@rule
class IncompatibleFieldType(Rule):
    code = "CW103"
    title = "Incompatible field type change"
    severity = Severity.ERROR
    description = "A field's type changed such that previously valid values are rejected."
    events = frozenset({Change.TYPE_CHANGED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        base, cur = event.before, event.after
        if not isinstance(base, Schema) or not isinstance(cur, Schema):
            return None
        name = event.pointer.rsplit(".", 1)[-1]
        return self.emit(
            event,
            f"Type of '{name}' changed from {base.describe()} to {cur.describe()}.",
            impact="Values allowed by the accepted contract are no longer compatible.",
            before=base.describe(),
            after=cur.describe(),
            remediation=Remediation(
                summary=f"Restore '{name}' to {base.describe()}, or widen it compatibly.",
                steps=[
                    f"Revert the annotation of '{name}' to {base.describe()}.",
                    "If the change is required, make the new type accept the old values "
                    "(e.g. a union that includes the previous type).",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
                example=f"{name}: {base.describe()}  # was  (now {cur.describe()})",
            ),
        )


@rule
class NewRequiredRequestField(Rule):
    code = "CW104"
    title = "Newly required request field"
    severity = Severity.ERROR
    description = "A request model gained a field with no default."
    events = frozenset({Change.FIELD_ADDED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        if event.direction != _REQUEST:
            return None
        field_ = event.after if isinstance(event.after, Field) else None
        if field_ is None or not field_.required:
            return None
        return self.emit(
            event,
            f"Request field '{field_.name}' is now required.",
            impact="Existing clients that omit this field will receive 422 validation errors.",
            remediation=Remediation(
                summary=f"Give '{field_.name}' a default so existing clients keep working.",
                steps=[
                    f"Find the request model for {event.endpoint}.",
                    f"Add a default value to '{field_.name}' (make it optional).",
                    f"If the field must be required, version the endpoint, then run: {_ACCEPT}",
                ],
                symbols=[field_.name],
                commands=[_ACCEPT],
                example=f"{field_.name}: {field_.schema.describe()} = <default>",
            ),
        )


@rule
class RequirednessChange(Rule):
    code = "CW105"
    title = "Incompatible requiredness or nullability change"
    severity = Severity.ERROR
    description = "A field became required where it was optional, or nullability flipped incompatibly."
    events = frozenset(
        {Change.REQUIRED_STRICTENED, Change.REQUIRED_RELAXED, Change.NULLABILITY_CHANGED}
    )

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        if event.kind is Change.REQUIRED_STRICTENED and event.direction == _REQUEST:
            return self._required(event, "is now required", "Existing clients may omit it.")
        if event.kind is Change.REQUIRED_RELAXED and event.direction == _RESPONSE:
            return self._required(event, "may now be absent", "Clients may assume it is present.")
        if event.kind is Change.NULLABILITY_CHANGED:
            return self._nullability(event)
        return None

    def _required(self, event: ChangeEvent, phrase: str, impact: str) -> Diagnostic:
        name = event.pointer.rsplit(".", 1)[-1]
        return self.emit(
            event,
            f"Field '{name}' {phrase}.",
            impact=impact,
            remediation=Remediation(
                summary=f"Keep '{name}' at its previous requiredness, or accept the change.",
                steps=[
                    f"Open the model used by {event.endpoint}.",
                    f"Adjust '{name}' so requiredness matches the accepted contract.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
            ),
        )

    def _nullability(self, event: ChangeEvent) -> Diagnostic | None:
        base, cur = event.before, event.after
        if not isinstance(base, Schema) or not isinstance(cur, Schema):
            return None
        breaking = (event.direction == _RESPONSE and cur.nullable) or (
            event.direction == _REQUEST and base.nullable
        )
        if not breaking:
            return None
        name = event.pointer.rsplit(".", 1)[-1]
        return self.emit(
            event,
            f"Nullability of '{name}' changed from {base.describe()} to {cur.describe()}.",
            impact="The accepted contract no longer guarantees the same null handling.",
            before=base.describe(),
            after=cur.describe(),
            remediation=Remediation(
                summary=f"Restore nullability of '{name}' to {base.describe()}.",
                steps=[
                    f"Revert the optional/required (nullable) annotation of '{name}'.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
                example=f"{name}: {base.describe()}  # was",
            ),
        )


@rule
class RemovedResponseStatus(Rule):
    code = "CW106"
    title = "Removed response status code"
    severity = Severity.ERROR
    description = "A response status code documented in the accepted contract is gone."
    events = frozenset({Change.STATUS_REMOVED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic:
        return self.emit(
            event,
            f"Response status '{event.pointer}' was removed from {event.endpoint}.",
            impact="Clients handling this status code will no longer receive it.",
            remediation=Remediation(
                summary=f"Re-add the '{event.pointer}' response to {event.endpoint}.",
                steps=[
                    f"Declare the '{event.pointer}' response on {event.endpoint} again.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[f"{event.pointer} {event.endpoint}"],
                commands=[_ACCEPT],
            ),
        )


@rule
class BreakingEnumChange(Rule):
    code = "CW107"
    title = "Breaking enum change"
    severity = Severity.ERROR
    description = "Enum values were narrowed for requests or widened for responses."
    events = frozenset({Change.ENUM_CHANGED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        base, cur = event.before, event.after
        if not isinstance(base, Schema) or not isinstance(cur, Schema):
            return None
        if event.direction == _REQUEST:
            breaking = base.enum is not None and not set(base.enum) <= set(cur.enum or ())
        else:
            breaking = cur.enum is not None and not set(cur.enum) <= set(base.enum or ())
        if not breaking:
            return None
        name = event.pointer.rsplit(".", 1)[-1]
        before = str(sorted(map(str, base.enum or ())))
        after = str(sorted(map(str, cur.enum or ())))
        return self.emit(
            event,
            f"Enum values for '{name}' changed incompatibly.",
            impact="Previously valid enum values are no longer accepted or returned as before.",
            before=before,
            after=after,
            remediation=Remediation(
                summary=f"Restore enum values of '{name}' to {before}.",
                steps=[
                    f"Restore the previous enum members for '{name}': {before}.",
                    "Only add new values (never remove) when widening a request enum.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
                example=f"{name}: {before}  # was  (now {after})",
            ),
        )


@rule
class IncompatibleSchemaChange(Rule):
    code = "CW108"
    title = "Incompatible schema change"
    severity = Severity.ERROR
    description = "A schema changed kind (object/array/scalar)."
    events = frozenset({Change.KIND_CHANGED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        base, cur = event.before, event.after
        if not isinstance(base, Schema) or not isinstance(cur, Schema):
            return None
        name = event.pointer.rsplit(".", 1)[-1]
        return self.emit(
            event,
            f"Shape of '{name}' changed from {base.describe()} to {cur.describe()}.",
            impact="The response or request shape is no longer compatible.",
            before=base.describe(),
            after=cur.describe(),
            remediation=Remediation(
                summary=f"Restore '{name}' to {base.describe()}.",
                steps=[
                    f"Revert '{name}' to its previous shape ({base.describe()}).",
                    "If a new shape is required, introduce a new field or endpoint instead.",
                    f"If intentional, run: {_ACCEPT}",
                ],
                symbols=[name],
                commands=[_ACCEPT],
            ),
        )


@rule
class ComponentReferenceChange(Rule):
    code = "CW204"
    title = "Component reference changed"
    severity = Severity.WARNING
    description = "A $ref target name changed; wire-compatible but may affect generated clients."
    events = frozenset({Change.REF_CHANGED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic:
        return self.emit(
            event,
            f"Component reference at '{event.pointer}' changed from {event.before} to {event.after}.",
            confidence=Confidence.POTENTIAL,
            impact="Generated clients that reference the component name may need regeneration.",
            remediation=Remediation(
                summary="Confirm the component rename is intentional.",
                steps=[
                    f"Verify the rename {event.before} -> {event.after} is deliberate.",
                    "Regenerate any generated API clients.",
                ],
                symbols=[str(event.after)],
            ),
        )


@rule
class CompatibleAddition(Rule):
    code = "CW110"
    title = "Compatible addition"
    severity = Severity.INFO
    description = "A purely additive change with no impact on existing clients."
    events = frozenset({Change.ENDPOINT_ADDED, Change.FIELD_ADDED})

    def evaluate(self, event: ChangeEvent) -> Diagnostic | None:
        if event.kind is Change.ENDPOINT_ADDED:
            return self.emit(
                event,
                f"Endpoint {event.endpoint} was added.",
                impact="No impact on existing clients.",
                remediation=Remediation(summary="No action required."),
            )
        field_ = event.after if isinstance(event.after, Field) else None
        if field_ is None or (event.direction == _REQUEST and field_.required):
            return None
        return self.emit(
            event,
            f"Field '{event.pointer}' was added compatibly.",
            impact="No impact on existing clients.",
            remediation=Remediation(summary="No action required."),
        )


def compare(baseline: Contract, current: Contract) -> list[Diagnostic]:
    """Run every registered rule over the contract diff and return findings."""
    findings: list[Diagnostic] = []
    rules = Rule.registry()
    for event in diff_contracts(baseline, current):
        for check_cls in rules:
            if event.kind in check_cls.events and (diagnostic := check_cls().evaluate(event)):
                findings.append(diagnostic)
    return sort_findings(findings)


def sort_findings(findings: Iterable[Diagnostic]) -> list[Diagnostic]:
    """Blocking errors first, then warnings, then info; stable within a group."""
    return sorted(
        findings,
        key=lambda d: (-d.severity.rank, d.code, d.endpoint or "", d.summary),
    )


__all__ = ["Change", "ChangeEvent", "Rule", "compare", "diff_contracts", "rule", "sort_findings"]
