"""Configuration loading, defaults, precedence, and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from contractwatch.config import DEFAULT_BASELINE, Configuration
from contractwatch.diagnostics import Severity
from contractwatch.errors import ConfigError, InvalidConfigurationError


def test_defaults_when_no_config(tmp_path: Path) -> None:
    config = Configuration.load(tmp_path)
    assert config.app is None
    assert config.baseline == DEFAULT_BASELINE
    assert config.severity_threshold is Severity.ERROR
    assert config.baseline_path == (tmp_path / DEFAULT_BASELINE).resolve()


def test_dedicated_toml_is_read_whole(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text('app = "app:app"\nmax_findings = 3\n', encoding="utf-8")
    config = Configuration.load(tmp_path)
    assert config.app == "app:app"
    assert config.max_findings == 3


def test_pyproject_table_is_read(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n\n[tool.contractwatch]\napp = "src.main:app"\n', encoding="utf-8"
    )
    assert Configuration.load(tmp_path).app == "src.main:app"


def test_dedicated_file_wins_over_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[tool.contractwatch]\napp = "py:app"\n', encoding="utf-8")
    (tmp_path / "contractwatch.toml").write_text('app = "dedicated:app"\n', encoding="utf-8")
    assert Configuration.load(tmp_path).app == "dedicated:app"


def test_overrides_win(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text('app = "app:app"\n', encoding="utf-8")
    config = Configuration.load(tmp_path, overrides={"app": "cli:app", "max_findings": None})
    assert config.app == "cli:app"


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text('nope = 1\n', encoding="utf-8")
    with pytest.raises(InvalidConfigurationError):
        Configuration.load(tmp_path)


def test_bad_severity_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text('severity_threshold = "loud"\n', encoding="utf-8")
    with pytest.raises(InvalidConfigurationError):
        Configuration.load(tmp_path)


def test_malformed_toml_raises_config_error(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text("this is = = broken", encoding="utf-8")
    with pytest.raises(ConfigError):
        Configuration.load(tmp_path)


def test_ignore_membership(tmp_path: Path) -> None:
    (tmp_path / "contractwatch.toml").write_text('ignore = ["CW110", "CW203"]\n', encoding="utf-8")
    config = Configuration.load(tmp_path)
    assert config.ignores("CW110")
    assert not config.ignores("CW101")
