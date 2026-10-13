"""ContractWatch CLI."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import typer
from rich.console import Console

from . import __version__
from .baseline import Baseline
from .config import Configuration
from .diagnostics import AnalysisResult
from .discovery import ProjectLayout
from .engine import Engine
from .errors import ContractWatchError, ReportError
from .fs import atomic_write_json, atomic_write_text
from .git import GitRepo
from .hooks import PreCommitManager
from .presentation import Capabilities, Renderer
from .reporting import Report, ReportStore

app = typer.Typer(
    help="ContractWatch: guard your FastAPI contract.",
    add_completion=False,
    no_args_is_help=True,
    invoke_without_command=False,
)
_console = Console(stderr=True)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"ContractWatch {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        None, "--version", "-v", help="Show version and exit.", callback=_version_callback, is_eager=True
    ),
) -> None:
    pass


@app.command()
def check(
    cwd: Path = typer.Option(
        Path("."), "--cwd", "-C", exists=True, file_okay=False, dir_okay=True, help="Project directory."
    ),
    app: str | None = typer.Option(None, "--app", help="Application target as 'package.module:app'."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Ignore the generation cache."),
    max_findings: int | None = typer.Option(None, "--max-findings", help="Override the finding limit."),
    json: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colour."),
    no_animation: bool = typer.Option(False, "--no-animation", help="Disable animations."),
    in_process: bool = typer.Option(False, "--in-process", hidden=True, help="Use the in-process loader."),
) -> None:
    """Compare the current contract against the baseline and report violations."""
    _guard(
        lambda: _check(
            cwd.resolve(), app, no_cache, max_findings, json, no_color, no_animation, in_process
        )
    )


def _check(
    cwd: Path,
    app_name: str | None,
    no_cache: bool,
    max_findings: int | None,
    json_output: bool,
    no_color: bool,
    no_animation: bool,
    in_process: bool,
) -> None:
    renderer, engine = _build(cwd, app_name, no_color, no_animation, in_process)
    if no_cache:
        engine.config.cache = False
    target = engine.layout.resolve_app(engine.config.app, app_name)
    baseline = _load_baseline_or_die(engine.config, renderer)
    result = engine.run(target, baseline=baseline, max_findings=max_findings)
    _persist_run(engine, target, result)
    if json_output:
        renderer.result_json(result, app=target, baseline_ref=result.baseline_ref)
    else:
        renderer.banner()
        renderer.result(result, app=target, baseline_ref=result.baseline_ref)
    raise typer.Exit(int(result.exit_code()))


@app.command()
def inspect(
    cwd: Path = typer.Option(
        Path("."), "--cwd", "-C", exists=True, file_okay=False, dir_okay=True, help="Project directory."
    ),
    app: str | None = typer.Option(None, "--app", help="Application target as 'package.module:app'."),
    json: bool = typer.Option(False, "--json", "-j", help="Emit machine-readable JSON."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colour."),
    no_animation: bool = typer.Option(False, "--no-animation", help="Disable animations."),
    in_process: bool = typer.Option(False, "--in-process", hidden=True, help="Use the in-process loader."),
) -> None:
    """Generate and display the current contract without comparing."""
    _guard(lambda: _inspect(cwd.resolve(), app, json, no_color, no_animation, in_process))


def _inspect(
    cwd: Path,
    app_name: str | None,
    json_output: bool,
    no_color: bool,
    no_animation: bool,
    in_process: bool,
) -> None:
    renderer, engine = _build(cwd, app_name, no_color, no_animation, in_process)
    target = engine.layout.resolve_app(engine.config.app, app_name)
    payload = engine.load_for_inspection(target)
    document = payload["openapi"]
    schemas = document.get("components", {}).get("schemas", {})
    paths = document.get("paths", {})
    operations = sum(1 for item in paths.values() if isinstance(item, dict) for _ in item)
    if json_output:
        renderer.console.print_json(data=payload)
        return
    renderer.banner()
    renderer.console.print(
        f"[bold]Contract for {target}[/]: {len(paths)} paths, {operations} operations, "
        f"{len(schemas)} schemas."
    )
    renderer.console.print(
        f"[dim]  Pydantic {payload.get('pydantic_version', '?')} · "
        f"FastAPI {payload.get('fastapi_version', '?')}[/]"
    )


@app.command()
def init(
    cwd: Path = typer.Option(
        Path("."), "--cwd", "-C", exists=True, file_okay=False, dir_okay=True, help="Project directory."
    ),
    app: str | None = typer.Option(None, "--app", help="Application target as 'package.module:app'."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Accept all prompts."),
    install_hook: bool = typer.Option(
        True, "--install-hook/--no-install-hook", help="Add the pre-commit hook."
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colour."),
    in_process: bool = typer.Option(False, "--in-process", hidden=True, help="Use the in-process loader."),
) -> None:
    """Accept the current contract as the baseline and wire up the pre-commit hook."""
    _guard(lambda: _init(cwd.resolve(), app, yes, install_hook, no_color, in_process))


def _init(
    cwd: Path,
    app_name: str | None,
    yes: bool,
    install_hook: bool,
    no_color: bool,
    in_process: bool,
) -> None:
    renderer, engine = _build(cwd, app_name, no_color, False, in_process)
    target = engine.layout.resolve_app(engine.config.app, app_name)
    _baseline_update(engine, target, renderer)
    if not install_hook:
        return
    manager = PreCommitManager(engine.layout.root)
    change = manager.ensure_hook()
    renderer.console.print(f"[dim]{change.detail}[/]")
    if engine.layout.git is None:
        renderer.console.print("[yellow]Not a Git repository; skipping `pre-commit install`.[/]")
        return
    if yes or _confirm("Install pre-commit hooks now?"):
        manager.install()
        renderer.console.print("[bold green]pre-commit hook installed.[/]")


@app.command("baseline")
def baseline_command(
    action: str = typer.Argument("update", help="One of: update."),
    cwd: Path = typer.Option(
        Path("."), "--cwd", "-C", exists=True, file_okay=False, dir_okay=True, help="Project directory."
    ),
    app: str | None = typer.Option(None, "--app", help="Application target as 'package.module:app'."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colour."),
    in_process: bool = typer.Option(False, "--in-process", hidden=True, help="Use the in-process loader."),
) -> None:
    """Manage the baseline."""
    if action != "update":
        raise typer.BadParameter("Only 'update' is supported.", param_hint="action")
    _guard(lambda: _baseline_command(cwd.resolve(), app, no_color, in_process))


def _baseline_command(cwd: Path, app_name: str | None, no_color: bool, in_process: bool) -> None:
    renderer, engine = _build(cwd, app_name, no_color, False, in_process)
    target = engine.layout.resolve_app(engine.config.app, app_name)
    _baseline_update(engine, target, renderer)


@app.command()
def report(
    latest: bool = typer.Option(False, "--latest", "-l", help="Show the most recent run."),
    list_reports: bool = typer.Option(False, "--list", "-L", help="List saved run reports."),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text or json."),
    output: Path | None = typer.Option(None, "--output", "-o", help="Export the report to this path."),
    open_latest: bool = typer.Option(False, "--open", help="Open the latest report in your editor."),
    json: bool = typer.Option(False, "--json", "-j", help="Alias for --format json."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colour."),
) -> None:
    """Inspect, export, or open previously persisted run reports."""
    _guard(
        lambda: _report(latest, list_reports, output_format, output, open_latest, json, no_color)
    )


def _report(
    show_latest: bool,
    show_list: bool,
    output_format: str,
    output: Path | None,
    open_latest: bool,
    json_output: bool,
    no_color: bool,
) -> None:
    fmt = "json" if json_output else output_format.lower()
    if fmt not in {"text", "json"}:
        raise typer.BadParameter("Must be 'text' or 'json'.", param_hint="--format")
    renderer = Renderer(Capabilities.detect(no_color=no_color, no_animation=True))
    store = ReportStore.default()
    if show_list:
        for path in store.history():
            renderer.console.print(path.name)
        return
    if open_latest:
        text_path = store.latest_text_path()
        if text_path is None:
            renderer.console.print("[red]No reports yet. Run `contractwatch check` first.[/]")
            raise typer.Exit(2)
        _open_in_editor(text_path)
        renderer.console.print(f"[dim]Opened {text_path}[/]")
        return
    entry = store.latest()
    if entry is None:
        renderer.console.print("[red]No reports yet. Run `contractwatch check` first.[/]")
        raise typer.Exit(2)
    if output is not None:
        _export_report(entry, output, fmt)
        renderer.console.print(f"[bold green]Report written to {output}[/]")
        return
    if fmt == "json":
        renderer.console.print_json(data=entry.to_dict())
    else:
        renderer.console.print(entry.to_text(), markup=False, highlight=False)


def _export_report(entry: Report, output: Path, fmt: str) -> None:
    if fmt == "json":
        atomic_write_json(output, entry.to_dict())
    else:
        atomic_write_text(output, entry.to_text())


def _open_in_editor(path: Path) -> None:
    """Open ``path`` with the OS default handler, only on explicit request."""
    target = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.run(["open", target], check=False)
        elif os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.run(["xdg-open", target], check=False)
    except OSError as exc:
        raise ReportError(f"Could not open {path}: {exc}") from exc


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #


def _build(
    cwd: Path,
    app_name: str | None,
    no_color: bool,
    no_animation: bool,
    in_process: bool,
) -> tuple[Renderer, Engine]:
    layout = ProjectLayout.discover(cwd)
    config = Configuration.load(layout.root, overrides=_overrides(app_name))
    engine = Engine.create(config, layout, prefer_subprocess=not in_process)
    renderer = Renderer(Capabilities.detect(no_color=no_color, no_animation=no_animation))
    return renderer, engine


def _overrides(app_name: str | None) -> dict[str, Any]:
    return {"app": app_name} if app_name else {}


def _baseline_update(engine: Engine, target: str, renderer: Renderer) -> None:
    payload = engine.load_for_inspection(target)
    baseline = Baseline.from_openapi(payload["openapi"], app=target)
    baseline.save(engine.config.baseline_path)
    renderer.console.print(f"[bold green]Baseline written to {engine.config.baseline_path}[/]")
    renderer.console.print(
        f"[dim]  Pydantic {payload.get('pydantic_version', '')} · "
        f"{len(baseline.contract().routes)} routes[/]"
    )


def _load_baseline_or_die(config: Configuration, renderer: Renderer) -> Baseline:
    if not Baseline.exists(config.baseline_path):
        renderer.console.print("[red]No baseline yet. Run `contractwatch init` first.[/]")
        raise typer.Exit(2)
    try:
        return Baseline.load(config.baseline_path)
    except ContractWatchError as exc:
        renderer.console.print(f"[red]{exc.render()}[/]")
        raise typer.Exit(2) from exc


def _persist_run(engine: Engine, target: str, result: AnalysisResult) -> None:
    layout = engine.layout
    commit = branch = None
    if layout.git is not None:
        commit = layout.git.revision()
        branch = _branch_name(layout.git)
    report = Report.from_run(
        result, app=target, project_root=str(layout.root), git_commit=commit, git_branch=branch
    )
    with suppress(ContractWatchError):
        engine.store.save(report, retention=engine.config.retention)


def _branch_name(git: GitRepo) -> str | None:
    return git.branch()


def _confirm(prompt: str) -> bool:
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return answer in {"y", "yes"}


def _guard(command: Callable[[], None]) -> None:
    try:
        command()
    except ContractWatchError as exc:
        _console.print(f"[bold red]ContractWatch: {exc.render()}[/]")
        raise typer.Exit(int(exc.exit_code)) from exc


def main() -> None:
    app()


if __name__ == "__main__":
    main()
