"""Cache correctness: reuse only when every input is unchanged."""

from __future__ import annotations

import os
from pathlib import Path

from contractwatch.cache import Cache, CacheKey, digest_file
from contractwatch.config import Configuration


def _key(tmp_path: Path, source: Path, **overrides: object) -> CacheKey:
    config = Configuration.load(tmp_path, overrides=dict(overrides))
    return CacheKey.for_run(
        tool_version="1.0.0",
        app="app:app",
        config=config,
        baseline_digest="base",
        source_roots=[source],
        root=tmp_path,
    )


def test_same_inputs_produce_same_key(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    assert _key(tmp_path, tmp_path).value == _key(tmp_path, tmp_path).value


def test_source_edit_changes_key(tmp_path: Path) -> None:
    source = tmp_path / "app.py"
    source.write_text("x = 1\n", encoding="utf-8")
    before = _key(tmp_path, tmp_path).value
    source.write_text("x = 2\n", encoding="utf-8")
    os.utime(source, (10_000, 10_000))
    assert _key(tmp_path, tmp_path).value != before


def test_config_change_changes_key(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert _key(tmp_path, tmp_path, max_findings=1).value != _key(tmp_path, tmp_path, max_findings=2).value


def test_baseline_change_changes_key(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    config = Configuration.load(tmp_path)
    first = CacheKey.for_run(
        tool_version="1", app="a", config=config, baseline_digest="A", source_roots=[tmp_path], root=tmp_path
    )
    second = CacheKey.for_run(
        tool_version="1", app="a", config=config, baseline_digest="B", source_roots=[tmp_path], root=tmp_path
    )
    assert first.value != second.value


def test_store_and_load_round_trip(tmp_path: Path) -> None:
    cache = Cache(tmp_path / ".cache")
    key = _key(tmp_path, tmp_path)
    assert cache.load(key) is None
    cache.store(key, {"openapi": {"paths": {}}})
    assert cache.load(key) == {"openapi": {"paths": {}}}


def test_corrupt_cache_entry_is_ignored(tmp_path: Path) -> None:
    cache = Cache(tmp_path / ".cache")
    key = _key(tmp_path, tmp_path)
    (cache.directory).mkdir(parents=True, exist_ok=True)
    (cache.directory / f"{key.value}.json").write_text("}{", encoding="utf-8")
    assert cache.load(key) is None


def test_digest_file_is_stable_and_content_addressed(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello")
    first = digest_file(path)
    assert first == digest_file(path)
    path.write_bytes(b"world")
    assert digest_file(path) != first


def test_missing_file_digests_to_empty(tmp_path: Path) -> None:
    assert digest_file(tmp_path / "nope") == ""
