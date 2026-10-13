"""Loader: payload building, exposure extraction, and error categorisation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from contractwatch.errors import AppLoadError
from contractwatch.loader import InProcessLoader, _extract_json, build_payload

APP = """
from fastapi import FastAPI
from pydantic import BaseModel


class Item(BaseModel):
    id: int


app = FastAPI()


@app.get("/items/{item_id}", response_model=Item)
def get_item(item_id: int) -> Item:
    return Item(id=item_id)


@app.post("/items")
def create_item(item: Item) -> Item:
    return item

not_an_app = object()
"""


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    (tmp_path / "svc_mod.py").write_text(APP, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("svc_mod", None)
    yield "svc_mod"
    sys.modules.pop("svc_mod", None)


def test_build_payload_ok(module: str) -> None:
    payload = build_payload(f"{module}:app")
    assert payload["ok"] is True
    assert "GET /items/{item_id}" in {f"{m.upper()} {p}" for m, p in _routes(payload["openapi"])}
    assert payload["pydantic_version"].startswith("2")
    assert payload["exposure"]


def test_exposure_links_response_component(module: str) -> None:
    payload = build_payload(f"{module}:app")
    records = {r["route"]: r for r in payload["exposure"]}
    get_record = records["GET /items/{item_id}"]
    assert get_record["response_models"] == [f"{module}.Item"]
    assert "Item" in get_record["response_components"]


def test_exposure_links_request_component(module: str) -> None:
    payload = build_payload(f"{module}:app")
    records = {r["route"]: r for r in payload["exposure"]}
    post_record = records["POST /items"]
    assert post_record["body_models"] == [f"{module}.Item"]


def test_bad_target_shape(module: str) -> None:
    payload = build_payload("justamodule")
    assert payload["ok"] is False
    assert payload["category"] == "AppLoadError"


def test_missing_attribute(module: str) -> None:
    payload = build_payload(f"{module}:missing")
    assert payload["ok"] is False


def test_non_fastapi_object(module: str) -> None:
    payload = build_payload(f"{module}:not_an_app")
    assert payload["ok"] is False
    assert "not a FastAPI" in payload["message"]


def test_missing_module() -> None:
    payload = build_payload("definitely.not.a.module:app")
    assert payload["ok"] is False


def test_in_process_loader_raises_on_error() -> None:
    loader = InProcessLoader()
    with pytest.raises(AppLoadError):
        loader.load("nope:app", root=Path("."))


def test_extract_json_finds_last_envelope() -> None:
    stdout = "some log line\n{\"ok\": true, \"openapi\": {}}\n"
    assert _extract_json(stdout) == {"ok": True, "openapi": {}}


def test_extract_json_ignores_noise() -> None:
    assert _extract_json("nope\nnot json") is None


def _routes(document: dict) -> list[tuple[str, str]]:
    routes: list[tuple[str, str]] = []
    for path, methods in document.get("paths", {}).items():
        for method in methods:
            routes.append((method, path))
    return routes
