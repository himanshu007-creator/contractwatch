"""Rule-level tests: every breaking-change code fires exactly when it should."""

from __future__ import annotations

from conftest import contract, json_response, openapi_doc, ref, request_body
from contractwatch.compare import compare
from contractwatch.diagnostics import Confidence, Severity


def findings(before: dict, after: dict):
    return compare(contract(before), contract(after))


def codes(items) -> set[str]:
    return {item.code for item in items}


ENDPOINT = "/items"


def _get(schema: dict) -> dict:
    return openapi_doc(paths={ENDPOINT: {"get": {"responses": {"200": json_response(schema)}}}})


def _post_prop(prop: dict) -> dict:
    body = {"type": "object", "properties": {"value": prop}}
    return openapi_doc(
        paths={ENDPOINT: {"post": {**request_body(body), "responses": {"200": json_response({"type": "object"})}}}}
    )


def _post(schema: dict) -> dict:
    return openapi_doc(
        paths={ENDPOINT: {"post": {**request_body(schema), "responses": {"200": json_response({"type": "object"})}}}}
    )


def test_cw101_removed_endpoint() -> None:
    result = findings(_get({"type": "object"}), openapi_doc())
    assert codes(result) == {"CW101"}
    assert result[0].severity is Severity.ERROR and result[0].blocking


def test_cw102_removed_response_field() -> None:
    before = _get({"type": "object", "properties": {"name": {"type": "string"}, "price": {"type": "number"}}})
    after = _get({"type": "object", "properties": {"name": {"type": "string"}}})
    result = findings(before, after)
    assert "CW102" in codes(result)


def test_cw103_incompatible_type_change() -> None:
    before = _post_prop({"oneOf": [{"type": "string"}, {"type": "integer"}]})
    after = _post_prop({"oneOf": [{"type": "string"}]})
    result = findings(before, after)
    assert "CW103" in codes(result)


def test_cw104_new_required_request_field() -> None:
    before = _post({"type": "object", "properties": {"name": {"type": "string"}}})
    after = _post(
        {"type": "object", "properties": {"name": {"type": "string"}, "sku": {"type": "string"}}, "required": ["sku"]}
    )
    result = findings(before, after)
    assert "CW104" in codes(result)


def test_cw105_request_requiredness_strictened() -> None:
    before = _post({"type": "object", "properties": {"name": {"type": "string"}}})
    after = _post({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
    result = findings(before, after)
    assert "CW105" in codes(result)


def test_cw106_removed_status() -> None:
    before = openapi_doc(
        paths={ENDPOINT: {"get": {"responses": {"200": json_response({"type": "object"}), "404": json_response({"type": "object"})}}}}
    )
    after = _get({"type": "object"})
    result = findings(before, after)
    assert "CW106" in codes(result)


def test_cw107_breaking_enum_narrowing_in_request() -> None:
    before = _post({"type": "object", "properties": {"kind": {"type": "string", "enum": ["a", "b", "c"]}}})
    after = _post({"type": "object", "properties": {"kind": {"type": "string", "enum": ["a"]}}})
    result = findings(before, after)
    assert "CW107" in codes(result)


def test_cw108_schema_kind_change() -> None:
    before = _get({"type": "object", "properties": {"x": {"type": "string"}}})
    after = _get({"type": "object", "properties": {"x": {"type": "object", "properties": {}}}})
    result = findings(before, after)
    assert "CW108" in codes(result)


def test_cw110_compatible_additions_are_info() -> None:
    before = _get({"type": "object", "properties": {"a": {"type": "string"}}})
    after = _get({"type": "object", "properties": {"a": {"type": "string"}, "b": {"type": "string"}}})
    result = findings(before, after)
    info = [f for f in result if f.code == "CW110"]
    assert info and all(f.severity is Severity.INFO for f in info)
    assert not any(f.blocking for f in result)


def test_cw204_component_ref_change_is_warning() -> None:
    before = _get(ref("Old"))
    after = openapi_doc(
        paths={ENDPOINT: {"get": {"responses": {"200": json_response(ref("New"))}}}},
        schemas={"New": {"type": "object", "properties": {}}},
    )
    result = findings(before, after)
    assert "CW204" in codes(result)
    warning = next(f for f in result if f.code == "CW204")
    assert warning.severity is Severity.WARNING
    assert warning.confidence is Confidence.POTENTIAL


def test_identical_contracts_produce_nothing() -> None:
    doc = _get(ref("Item"))
    doc["components"]["schemas"]["Item"] = {"type": "object", "properties": {"id": {"type": "integer"}}}
    assert findings(doc, doc) == []
