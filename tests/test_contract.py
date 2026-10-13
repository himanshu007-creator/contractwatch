"""Normalisation of OpenAPI documents into comparable contracts."""

from __future__ import annotations

from conftest import contract, json_response, openapi_doc, ref
from contractwatch.contract import Schema


def test_ref_is_resolved_to_target_shape() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response(ref("Item"))}}}},
        schemas={"Item": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.is_object()
    assert response.fields["id"].required
    assert response.fields["id"].schema.type == "integer"


def test_all_of_is_merged() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response({"allOf": [ref("A"), ref("B")]})}}}},
        schemas={
            "A": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
            "B": {"type": "object", "properties": {"b": {"type": "integer"}}},
        },
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert set(response.fields) == {"a", "b"}
    assert response.fields["a"].required


def test_one_of_becomes_union() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response({"oneOf": [{"type": "string"}, {"type": "integer"}]})}}}}
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.type == "union"
    assert {v.type for v in response.variants} == {"string", "integer"}


def test_nullable_30_dialect() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response({"type": "string", "nullable": True})}}}},
        version="3.0.3",
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.nullable
    assert response.type == "string"


def test_nullable_31_type_list() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response({"type": ["string", "null"]})}}}}
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.nullable
    assert response.type == "string"


def test_array_items_are_normalised() -> None:
    doc = openapi_doc(paths={"/x": {"get": {"responses": {"200": json_response({"type": "array", "items": ref("Item")})}}}},
                      schemas={"Item": {"type": "object", "properties": {"id": {"type": "integer"}}}})
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.type == "array"
    assert response.items is not None and response.items.is_object()


def test_recursive_ref_terminates() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response(ref("Node"))}}}},
        schemas={"Node": {"type": "object", "properties": {"next": ref("Node")}}},
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.is_object()
    assert "next" in response.fields


def test_enum_is_captured() -> None:
    doc = openapi_doc(
        paths={"/x": {"get": {"responses": {"200": json_response({"type": "string", "enum": ["a", "b"]})}}}}
    )
    response = contract(doc).routes["GET /x"].responses["200"]
    assert response.enum == ("a", "b")
    assert isinstance(response, Schema)
