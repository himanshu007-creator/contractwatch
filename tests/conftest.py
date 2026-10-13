"""Shared fixtures and builders for the ContractWatch test suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from contractwatch.cache import Cache
from contractwatch.config import Configuration
from contractwatch.contract import Contract
from contractwatch.diagnostics import AnalysisResult
from contractwatch.discovery import ProjectLayout
from contractwatch.engine import Engine
from contractwatch.loader import AppLoader, LoadedApp
from contractwatch.reporting import Report, ReportStore

OPENAPI = "3.1.0"


def openapi_doc(
    paths: dict[str, Any] | None = None,
    schemas: dict[str, Any] | None = None,
    *,
    version: str = OPENAPI,
) -> dict[str, Any]:
    return {
        "openapi": version,
        "info": {"title": "demo", "version": "1"},
        "paths": paths or {},
        "components": {"schemas": schemas or {}},
    }


def contract(document: dict[str, Any]) -> Contract:
    return Contract.from_openapi(document)


def ref(name: str) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{name}"}


def json_response(schema: dict[str, Any]) -> dict[str, Any]:
    return {"content": {"application/json": {"schema": schema}}}


def request_body(schema: dict[str, Any]) -> dict[str, Any]:
    return {"requestBody": {"content": {"application/json": {"schema": schema}}}}


def make_layout(root: Path) -> ProjectLayout:
    return ProjectLayout(
        root=root,
        package_manager="pip",
        lockfile=None,
        git=None,
        has_precommit_config=(root / ".pre-commit-config.yaml").exists(),
        source_roots=(root,),
        app_candidates=("app:app",),
    )


class StubLoader(AppLoader):
    """In-memory loader for engine tests; optionally raises or returns a payload."""

    def __init__(
        self,
        document: dict[str, Any] | None = None,
        *,
        error: Exception | None = None,
        pydantic_version: str = "2.14.1",
    ) -> None:
        self.document = document
        self.error = error
        self.pydantic_version = pydantic_version
        self.calls = 0

    def load(self, target: str, *, root: Path, sys_paths: tuple[str, ...] | list[str] = ()) -> LoadedApp:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return LoadedApp(
            openapi=self.document or {},
            pydantic_version=self.pydantic_version,
            fastapi_version="0.143.0",
        )


def make_engine(
    tmp_path: Path,
    loader: AppLoader,
    *,
    config_overrides: dict[str, Any] | None = None,
) -> Engine:
    config = Configuration.load(tmp_path, overrides=config_overrides or {})
    layout = make_layout(tmp_path)
    return Engine(
        config=config,
        layout=layout,
        loader=loader,
        cache=Cache(tmp_path / ".cache"),
        store=ReportStore(tmp_path / "reports"),
    )


@pytest.fixture
def stub_report() -> Report:
    return Report.from_run(AnalysisResult(), app="app:app", project_root="/tmp/x")


def write_project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def dump(document: dict[str, Any]) -> str:
    return json.dumps(document)
