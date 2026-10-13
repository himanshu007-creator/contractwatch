"""Baseline lifecycle: round-trip, atomic write, and failure modes."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from conftest import openapi_doc
from contractwatch.baseline import SCHEMA_VERSION, Baseline
from contractwatch.errors import BaselineError


def test_missing_baseline_raises_with_code(tmp_path: Path) -> None:
    with pytest.raises(BaselineError) as info:
        Baseline.load(tmp_path / "nope.json")
    assert info.value.code == "CW401"


def test_malformed_baseline_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(BaselineError):
        Baseline.load(path)


def test_baseline_without_openapi_raises(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
    with pytest.raises(BaselineError):
        Baseline.load(path)


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / ".contractwatch" / "baseline.json"
    document = openapi_doc(paths={"/x": {"get": {"responses": {"200": {"description": "ok"}}}}})
    Baseline.from_openapi(document, app="app:app").save(path)
    loaded = Baseline.load(path)
    assert loaded.openapi == document
    assert loaded.app == "app:app"
    assert loaded.schema_version == SCHEMA_VERSION
    assert loaded.created


def test_save_is_atomic_and_private(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    Baseline.from_openapi(openapi_doc()).save(path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode & 0o077 == 0  # no group/other access
    assert not list(tmp_path.glob("*.tmp*"))


def test_exists(tmp_path: Path) -> None:
    path = tmp_path / "baseline.json"
    assert not Baseline.exists(path)
    Baseline.from_openapi(openapi_doc()).save(path)
    assert Baseline.exists(path)


def test_contract_is_reconstructed(tmp_path: Path) -> None:
    document = openapi_doc(paths={"/x": {"get": {"responses": {"200": {"description": "ok"}}}}})
    assert "GET /x" in Baseline.from_openapi(document).contract().routes
