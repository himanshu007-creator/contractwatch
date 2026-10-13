"""Pre-commit hook management: idempotency, preservation, and safety."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from contractwatch.errors import HookError
from contractwatch.hooks import HOOK_ID, PRECOMMIT_FILE, PreCommitManager

EXISTING = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
"""


def _load(root: Path) -> dict:
    return yaml.safe_load((root / PRECOMMIT_FILE).read_text(encoding="utf-8"))


def test_creates_config_when_absent(tmp_path: Path) -> None:
    change = PreCommitManager(tmp_path).ensure_hook()
    assert change.created and change.changed
    document = _load(tmp_path)
    assert document["repos"][0]["repo"] == "local"
    assert document["repos"][0]["hooks"][0]["id"] == HOOK_ID


def test_is_idempotent(tmp_path: Path) -> None:
    manager = PreCommitManager(tmp_path)
    manager.ensure_hook()
    second = manager.ensure_hook()
    assert not second.changed
    assert len(_load(tmp_path)["repos"]) == 1


def test_preserves_existing_hooks(tmp_path: Path) -> None:
    (tmp_path / PRECOMMIT_FILE).write_text(EXISTING, encoding="utf-8")
    PreCommitManager(tmp_path).ensure_hook()
    document = _load(tmp_path)
    assert document["repos"][0]["repo"].startswith("https://github.com/astral-sh")
    assert document["repos"][-1]["hooks"][0]["id"] == HOOK_ID


def test_has_hook_reflects_state(tmp_path: Path) -> None:
    manager = PreCommitManager(tmp_path)
    assert not manager.has_hook()
    manager.ensure_hook()
    assert manager.has_hook()


def test_malformed_yaml_is_reported_and_left_unchanged(tmp_path: Path) -> None:
    path = tmp_path / PRECOMMIT_FILE
    path.write_text("repos: [unterminated\n", encoding="utf-8")
    with pytest.raises(HookError) as info:
        PreCommitManager(tmp_path).ensure_hook()
    assert info.value.code == "CW403"
    assert path.read_text(encoding="utf-8") == "repos: [unterminated\n"


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    (tmp_path / PRECOMMIT_FILE).write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(HookError):
        PreCommitManager(tmp_path).ensure_hook()


def test_install_delegates_to_runner(tmp_path: Path) -> None:
    class Runner:
        def __init__(self) -> None:
            self.commands: list[list[str]] = []

        def run(self, args, cwd):  # type: ignore[no-untyped-def]
            self.commands.append(list(args))
            from contractwatch.git import CommandResult

            return CommandResult(0, "", "")

    runner = Runner()
    PreCommitManager(tmp_path, runner=runner).install()  # type: ignore[arg-type]
    assert runner.commands == [["install"]]


def test_install_failure_raises_hook_error(tmp_path: Path) -> None:
    class Runner:
        def run(self, args, cwd):  # type: ignore[no-untyped-def]
            from contractwatch.git import CommandResult

            return CommandResult(1, "", "boom")

    with pytest.raises(HookError):
        PreCommitManager(tmp_path, runner=Runner()).install()  # type: ignore[arg-type]
