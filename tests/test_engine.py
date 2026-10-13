"""Engine orchestration: exit codes, error mapping, caching, filtering."""

from __future__ import annotations

from pathlib import Path

from conftest import StubLoader, json_response, make_engine, openapi_doc, ref
from contractwatch.baseline import Baseline
from contractwatch.config import Configuration
from contractwatch.errors import AppLoadError, OpenAPIGenerationError, PydanticCompatibilityError


def _current() -> dict:
    return openapi_doc(
        paths={"/items": {"get": {"responses": {"200": json_response(ref("Item"))}}}},
        schemas={"Item": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
    )


def _baseline(document: dict) -> Baseline:
    return Baseline.from_openapi(document, app="app:app")


def test_clean_run_exits_ok(tmp_path: Path) -> None:
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader)
    result = engine.run("app:app", baseline=_baseline(_current()))
    assert result.exit_code() == 0
    assert result.routes_inspected == 1


def test_breaking_change_blocks(tmp_path: Path) -> None:
    baseline = openapi_doc(
        paths={"/items": {"get": {"responses": {"200": json_response(ref("Item"))}}}},
        schemas={
            "Item": {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "price": {"type": "number"}},
                "required": ["id"],
            }
        },
    )
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader)
    result = engine.run("app:app", baseline=_baseline(baseline))
    assert result.exit_code() == 1
    assert any(f.code == "CW102" for f in result.findings)


def test_missing_baseline_is_an_error(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, StubLoader(_current()))
    result = engine.run("app:app", baseline=None)
    assert result.exit_code() == 2
    assert result.errors[0].code == "CW401"


def test_app_load_error_maps_to_cw303(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, StubLoader(error=AppLoadError("boom", target="app:app")))
    result = engine.run("app:app", baseline=_baseline(_current()))
    assert result.exit_code() == 2
    assert result.errors[0].code == "CW303"


def test_pydantic_error_maps_to_cw301(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, StubLoader(error=PydanticCompatibilityError("v1")))
    result = engine.run("app:app", baseline=_baseline(_current()))
    assert result.errors[0].code == "CW301"


def test_openapi_error_maps_to_cw304(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, StubLoader(error=OpenAPIGenerationError("bad")))
    result = engine.run("app:app", baseline=_baseline(_current()))
    assert result.errors[0].code == "CW304"


def test_ignore_filters_findings(tmp_path: Path) -> None:
    baseline = openapi_doc(paths={}, schemas={})
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader, config_overrides={"ignore": ["CW101"]})
    result = engine.run("app:app", baseline=_baseline(baseline))
    assert not any(f.code == "CW101" for f in result.findings)


def test_max_findings_truncates_and_records_skips(tmp_path: Path) -> None:
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader, config_overrides={"max_findings": 1})
    fat_baseline = openapi_doc(
        paths={"/items": {"get": {"responses": {"200": json_response(ref("Item"))}}}},
        schemas={
            "Item": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "x": {"type": "string"},
                    "y": {"type": "string"},
                    "z": {"type": "string"},
                },
                "required": ["id"],
            }
        },
    )
    result = engine.run("app:app", baseline=_baseline(fat_baseline))
    assert len(result.findings) == 1
    assert result.skipped


def test_cache_prevents_second_load(tmp_path: Path) -> None:
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader)
    engine.run("app:app", baseline=_baseline(_current()))
    engine.run("app:app", baseline=_baseline(_current()))
    assert loader.calls == 1


def test_cache_disabled_always_loads(tmp_path: Path) -> None:
    loader = StubLoader(_current())
    engine = make_engine(tmp_path, loader, config_overrides={"cache": False})
    engine.run("app:app", baseline=_baseline(_current()))
    engine.run("app:app", baseline=_baseline(_current()))
    assert loader.calls == 2


def test_environment_reports_v1(tmp_path: Path) -> None:
    loader = StubLoader(_current(), pydantic_version="1.10.13")
    engine = make_engine(tmp_path, loader, config_overrides={"cache": False})
    result = engine.run("app:app", baseline=_baseline(_current()))
    assert any(error.code == "CW301" for error in result.errors)


def test_inspection_raises_on_failure(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, StubLoader(error=AppLoadError("nope", target="app:app")))
    import pytest

    with pytest.raises(AppLoadError):
        engine.load_for_inspection("app:app")


def test_engine_create_wires_defaults(tmp_path: Path) -> None:
    from conftest import make_layout
    from contractwatch.engine import Engine

    config = Configuration.load(tmp_path)
    engine = Engine.create(config, make_layout(tmp_path), prefer_subprocess=False, cache_directory=tmp_path / "c")
    assert engine.cache.directory == tmp_path / "c"
    assert not isinstance(engine.loader, StubLoader)
