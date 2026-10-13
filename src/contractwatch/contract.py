"""Framework-independent, normalized representation of an API contract.

OpenAPI documents are noisy: ``$ref`` indirection, ``allOf`` composition, and
two different spellings of nullability. This module collapses all of that into a
small, comparison-friendly tree so the rules in :mod:`contractwatch.compare`
never touch raw OpenAPI.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

_MAX_DEPTH = 16
_JSON_MEDIA = "application/json"


@dataclass(slots=True)
class Field:
    name: str
    schema: Schema
    required: bool

    def describe(self) -> str:
        marker = "" if self.required else "?"
        return f"{marker}{self.name}: {self.schema.describe()}"


@dataclass(slots=True)
class Schema:
    type: str | None = None
    fields: dict[str, Field] = field(default_factory=dict)
    items: Schema | None = None
    variants: tuple[Schema, ...] = ()
    enum: tuple[Any, ...] | None = None
    fmt: str | None = None
    nullable: bool = False
    ref: str | None = None

    def describe(self) -> str:
        base: str
        if self.ref:
            base = self.ref
        elif self.type == "array":
            base = f"array[{self.items.describe() if self.items else 'any'}]"
        elif self.type == "object" or self.fields:
            base = "object"
        elif self.variants:
            base = "|".join(v.describe() for v in self.variants)
        else:
            base = self.type or "any"
        if self.enum:
            base += "(" + ",".join(str(e) for e in self.enum) + ")"
        return f"{base}?" if self.nullable else base

    def is_object(self) -> bool:
        return self.type == "object" or bool(self.fields)


@dataclass(slots=True)
class Route:
    method: str
    path: str
    operation_id: str | None
    request_fields: dict[str, Field]
    responses: dict[str, Schema]
    tags: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return f"{self.method.upper()} {self.path}"


@dataclass(slots=True)
class Contract:
    openapi_version: str
    routes: dict[str, Route]
    schemas: dict[str, Schema]
    raw: Mapping[str, Any]

    @classmethod
    def from_openapi(cls, document: Mapping[str, Any]) -> Contract:
        """Factory: normalize an OpenAPI document into a comparable contract."""
        normalizer = _SchemaNormalizer(document.get("components", {}).get("schemas", {}))
        schemas = {
            name: normalizer.normalize(node)
            for name, node in document.get("components", {}).get("schemas", {}).items()
        }
        routes: dict[str, Route] = {}
        for path, item in document.get("paths", {}).items():
            for method, operation in item.items():
                if method.lower() not in _HTTP_METHODS:
                    continue
                route = _build_route(method, path, operation, normalizer)
                routes[route.key] = route
        return cls(
            openapi_version=str(document.get("openapi", "")),
            routes=routes,
            schemas=schemas,
            raw=document,
        )


_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


def _build_route(
    method: str, path: str, operation: Mapping[str, Any], normalizer: _SchemaNormalizer
) -> Route:
    request_fields: dict[str, Field] = {}
    body = operation.get("requestBody") or {}
    body_schema = _pick_schema(body.get("content", {}))
    if body_schema is not None:
        normalized = normalizer.normalize(body_schema)
        request_fields.update(normalized.fields)
    for parameter in operation.get("parameters", []):
        if parameter.get("in") == "path":
            continue
        if not parameter.get("required"):
            continue
        name = parameter.get("name")
        if name:
            request_fields[name] = Field(name, normalizer.normalize(parameter.get("schema", {})), True)

    responses: dict[str, Schema] = {}
    for status, response in (operation.get("responses") or {}).items():
        schema_node = _pick_schema((response or {}).get("content", {}))
        responses[str(status)] = (
            normalizer.normalize(schema_node) if schema_node is not None else Schema()
        )
    return Route(
        method=method.upper(),
        path=path,
        operation_id=operation.get("operationId"),
        request_fields=request_fields,
        responses=responses,
        tags=tuple(operation.get("tags", ())),
    )


def _pick_schema(content: Mapping[str, Any]) -> Any | None:
    if not content:
        return None
    media = content.get(_JSON_MEDIA) or next(iter(content.values()))
    return media.get("schema") if isinstance(media, Mapping) else None


class _SchemaNormalizer:
    """Resolves ``$ref``/composition into :class:`Schema` with cycle protection."""

    def __init__(self, components: Mapping[str, Any]) -> None:
        self._components = components
        self._active: set[str] = set()

    def normalize(self, node: Any, *, depth: int = 0) -> Schema:
        if not isinstance(node, Mapping) or depth > _MAX_DEPTH:
            return Schema()

        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in self._active:
                return Schema(ref=name)
            target = self._components.get(name)
            if target is None:
                return Schema(ref=name)
            self._active.add(name)
            try:
                resolved = self.normalize(target, depth=depth + 1)
            finally:
                self._active.discard(name)
            resolved.ref = name
            return resolved

        if "allOf" in node:
            return self._merge_all_of(node, depth)
        if "oneOf" in node or "anyOf" in node:
            key = "oneOf" if "oneOf" in node else "anyOf"
            return Schema(
                type="union",
                variants=tuple(self.normalize(v, depth=depth + 1) for v in node[key]),
                nullable=bool(node.get("nullable")),
            )

        return self._leaf(node, depth)

    def _merge_all_of(self, node: Mapping[str, Any], depth: int) -> Schema:
        merged = Schema()
        for part in node["allOf"]:
            resolved = self.normalize(part, depth=depth + 1)
            merged.fields.update(resolved.fields)
            if resolved.type and not merged.type:
                merged.type = resolved.type
            if resolved.nullable:
                merged.nullable = True
            if resolved.ref and not merged.ref:
                merged.ref = resolved.ref
        if node.get("properties") or node.get("required"):
            leaf = self._leaf({k: v for k, v in node.items() if k != "allOf"}, depth)
            merged.fields.update(leaf.fields)
            if leaf.type and not merged.type:
                merged.type = leaf.type
        if merged.fields and not merged.type:
            merged.type = "object"
        return merged

    def _leaf(self, node: Mapping[str, Any], depth: int) -> Schema:
        declared = node.get("type")
        nullable = bool(node.get("nullable"))
        if isinstance(declared, list):
            nullable = nullable or "null" in declared
            named = [t for t in declared if t != "null"]
            declared = named[0] if named else None

        schema = Schema(
            type=declared,
            enum=tuple(node["enum"]) if node.get("enum") else None,
            fmt=node.get("format"),
            nullable=nullable,
        )
        if node.get("properties") is not None or schema.type == "object":
            schema.type = schema.type or "object"
            required = set(node.get("required", ()))
            schema.fields = {
                name: Field(name, self.normalize(prop, depth=depth + 1), name in required)
                for name, prop in node.get("properties", {}).items()
            }
        if "items" in node:
            schema.type = "array"
            schema.items = self.normalize(node["items"], depth=depth + 1)
        return schema


__all__ = ["Contract", "Field", "Route", "Schema"]
