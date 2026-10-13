from __future__ import annotations

from pathlib import Path

from contractwatch.discovery import ProjectLayout
from contractwatch.reliability import ReliabilityContext, run_reliability
from contractwatch.source import SourceIndex


def _layout(tmp_path: Path) -> ProjectLayout:
    return ProjectLayout(
        root=tmp_path,
        package_manager="uv",
        lockfile=None,
        git=None,
        has_precommit_config=False,
        source_roots=(tmp_path,),
        app_candidates=("app:app",),
    )


def test_rl101_blocks_in_async(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_text(
        "import time\n"
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/x')\n"
        "async def x():\n"
        "    time.sleep(1)\n",
        encoding="utf-8",
    )
    layout = _layout(tmp_path)
    index = SourceIndex.build(tmp_path)
    findings = run_reliability(ReliabilityContext(layout=layout, index=index, openapi={}))
    assert any(f.code == "RL101" for f in findings)


def test_rl102_missing_timeout(tmp_path: Path) -> None:
    app = tmp_path / "app.py"
    app.write_text(
        "import requests\n"
        "from fastapi import FastAPI\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/x')\n"
        "def x():\n"
        "    return requests.get('http://x')\n",
        encoding="utf-8",
    )
    layout = _layout(tmp_path)
    index = SourceIndex.build(tmp_path)
    findings = run_reliability(ReliabilityContext(layout=layout, index=index, openapi={}))
    assert any(f.code == "RL102" for f in findings)


def test_rl103_duplicate_routes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    index = SourceIndex.build(tmp_path)
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {"/x": {"get": [{}, {}]}},
    }
    findings = run_reliability(ReliabilityContext(layout=layout, index=index, openapi=openapi))
    assert any(f.code == "RL103" for f in findings)


def test_rl104_conflicting_operation_ids(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    index = SourceIndex.build(tmp_path)
    openapi = {
        "openapi": "3.1.0",
        "info": {"title": "t", "version": "1"},
        "paths": {
            "/x": {"get": {"operationId": "op"}},
            "/y": {"get": {"operationId": "op"}},
        },
    }
    findings = run_reliability(ReliabilityContext(layout=layout, index=index, openapi=openapi))
    assert any(f.code == "RL104" for f in findings)
