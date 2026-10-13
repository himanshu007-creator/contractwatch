from pathlib import Path

import pytest

from contractwatch.loader import SubprocessLoader

pytestmark = pytest.mark.slow


def test_subprocess_loader_handles_many_routes(tmp_path: Path) -> None:
    app = tmp_path / "app_bench.py"
    app.write_text(
        "from fastapi import FastAPI\nfrom pydantic import BaseModel\n\napp = FastAPI()\n\n"
        "class Item(BaseModel):\n    id: int\n\n"
        + "\n".join(f'@app.get("/r{i}", response_model=Item)\ndef r{i}(i:int={i})->Item: return Item(id=i)' for i in range(50)),
        encoding="utf-8",
    )
    loaded = SubprocessLoader().load("app_bench:app", root=tmp_path, sys_paths=[str(tmp_path)])
    assert len(loaded.openapi["paths"]) == 50
