"""Git access via an injectable runner; no real repository is touched."""

from __future__ import annotations

from pathlib import Path

from contractwatch.git import CommandResult, GitRepo


class FakeRunner:
    def __init__(self, responses: dict[tuple[str, ...], CommandResult]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, ...]] = []

    def run(self, args, cwd: Path) -> CommandResult:  # type: ignore[no-untyped-def]
        key = tuple(args)
        self.calls.append(key)
        return self.responses.get(key, CommandResult(1, "", "unknown"))


def test_discover_returns_repo_with_toplevel(tmp_path: Path) -> None:
    runner = FakeRunner({("rev-parse", "--show-toplevel"): CommandResult(0, "/repo", "")})
    repo = GitRepo.discover(tmp_path, runner=runner)
    assert repo is not None
    assert repo.root == Path("/repo")


def test_discover_returns_none_outside_repo(tmp_path: Path) -> None:
    runner = FakeRunner({})
    assert GitRepo.discover(tmp_path, runner=runner) is None


def test_revision(tmp_path: Path) -> None:
    runner = FakeRunner({("rev-parse", "--verify", "--quiet", "HEAD"): CommandResult(0, "abc123", "")})
    assert GitRepo(tmp_path, runner=runner).revision() == "abc123"


def test_branch(tmp_path: Path) -> None:
    runner = FakeRunner({("rev-parse", "--abbrev-ref", "HEAD"): CommandResult(0, "feature/x", "")})
    assert GitRepo(tmp_path, runner=runner).branch() == "feature/x"


def test_is_dirty(tmp_path: Path) -> None:
    runner = FakeRunner({("status", "--porcelain"): CommandResult(0, " M app.py", "")})
    assert GitRepo(tmp_path, runner=runner).is_dirty()


def test_staged_paths(tmp_path: Path) -> None:
    runner = FakeRunner({("diff", "--cached", "--name-only", "--diff-filter=ACMR"): CommandResult(0, "a.py\nb.py", "")})
    assert GitRepo(tmp_path, runner=runner).staged_paths() == ["a.py", "b.py"]


def test_changed_paths_on_failure_is_empty(tmp_path: Path) -> None:
    assert GitRepo(tmp_path, runner=FakeRunner({})).changed_paths("main") == []


def test_read_file_at(tmp_path: Path) -> None:
    runner = FakeRunner({("show", "HEAD:app.py"): CommandResult(0, "print(1)", "")})
    assert GitRepo(tmp_path, runner=runner).read_file_at("HEAD", "app.py") == "print(1)"


def test_read_file_at_missing(tmp_path: Path) -> None:
    assert GitRepo(tmp_path, runner=FakeRunner({})).read_file_at("HEAD", "nope.py") is None
