"""Report store: round-trip, history ordering, retention, and redaction."""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from contractwatch.config import Retention
from contractwatch.diagnostics import AnalysisError, AnalysisResult, Diagnostic, Severity
from contractwatch.reporting import Report, ReportStore
from contractwatch.reporting.redact import redact, redact_url


def _report(**kwargs: object) -> Report:
    return Report.from_run(AnalysisResult(), app="app:app", project_root="/tmp/x", **kwargs)  # type: ignore[arg-type]


def test_save_and_latest_round_trip(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    store.save(_report())
    latest = store.latest()
    assert latest is not None
    assert latest.app == "app:app"


def test_latest_is_none_when_empty(tmp_path: Path) -> None:
    assert ReportStore(tmp_path / "reports").latest() is None


def test_findings_survive_round_trip(tmp_path: Path) -> None:
    from contractwatch.diagnostics import Confidence, Remediation

    finding = Diagnostic(
        code="CW102",
        severity=Severity.ERROR,
        confidence=Confidence.CONFIRMED,
        title="Removed response field",
        summary="price removed",
        endpoint="GET /x",
        remediation=Remediation(summary="fix", steps=["a"], commands=["contractwatch check"]),
    )
    store = ReportStore(tmp_path / "reports")
    store.save(Report.from_run(AnalysisResult(findings=[finding]), app="app:app", project_root="/tmp"))
    restored = store.latest()
    assert restored is not None
    assert restored.result.findings[0].code == "CW102"
    assert restored.result.findings[0].remediation is not None
    assert restored.result.findings[0].remediation.commands == ("contractwatch check",)


def test_analysis_errors_survive_round_trip(tmp_path: Path) -> None:
    error = AnalysisError(code="CW303", message="import failed", detail="traceback")
    store = ReportStore(tmp_path / "reports")
    store.save(Report.from_run(AnalysisResult(errors=[error]), app="app:app", project_root="/tmp"))
    restored = store.latest()
    assert restored is not None
    assert restored.result.errors[0].code == "CW303"


def test_history_excludes_latest(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    store.save(_report())
    time.sleep(0.01)
    store.save(_report())
    history = store.history()
    assert len(history) == 2
    assert all(path.name != "latest.json" for path in history)


def test_retention_prunes_old_entries(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    for _ in range(5):
        store.save(_report())
        time.sleep(0.01)
    old = time.time() - 40 * 24 * 3600
    for path in store.history():
        os.utime(path, (old, old))
    store.save(_report(), retention=Retention(keep=1, days=7))
    assert len(store.history()) == 1


def test_reports_are_private(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    path = store.save(_report())
    assert stat.S_IMODE(os.stat(path).st_mode) & 0o077 == 0


def test_redact_masks_known_secrets() -> None:
    text = "token=abc123 api_key=deadbeef password=hunter2"
    masked = redact(text)
    assert "abc123" not in masked
    assert "deadbeef" not in masked
    assert "hunter2" not in masked


def test_redact_url_strips_credentials() -> None:
    assert redact_url("https://user:pass@example.com/path") == "https://example.com/path"


def test_default_home_respects_env(tmp_path: Path, monkeypatch) -> None:
    from contractwatch.reporting import store as store_module

    monkeypatch.setenv("CONTRACTWATCH_HOME", str(tmp_path))
    assert store_module.default_home() == tmp_path


def test_save_writes_text_and_json(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    json_path = store.save(_report())
    text_path = json_path.with_suffix(".txt")
    assert json_path.is_file()
    assert text_path.is_file()
    assert "ContractWatch" in text_path.read_text(encoding="utf-8")


def test_latest_text_path_points_at_newest(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    store.save(_report())
    time.sleep(0.01)
    newest = store.save(_report())
    assert store.latest_text_path() == newest.with_suffix(".txt")


def test_latest_text_path_none_when_empty(tmp_path: Path) -> None:
    assert ReportStore(tmp_path / "reports").latest_text_path() is None


def test_text_report_carries_findings(tmp_path: Path) -> None:
    from contractwatch.diagnostics import Confidence, Remediation

    finding = Diagnostic(
        code="CW102",
        severity=Severity.ERROR,
        confidence=Confidence.CONFIRMED,
        title="Removed response field",
        summary="price removed from GET /x",
        endpoint="GET /x",
        remediation=Remediation(summary="Restore the field", commands=["contractwatch baseline update"]),
    )
    report = Report.from_run(
        AnalysisResult(findings=[finding], baseline_ref="bl_1"),
        app="app:app",
        project_root="/tmp",
    )
    text = report.to_text()
    assert "CW102" in text
    assert "price removed from GET /x" in text
    assert "Restore the field" in text
    assert "contractwatch baseline update" in text
    assert "bl_1" in text


def test_retention_prunes_text_reports(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    for _ in range(5):
        store.save(_report())
        time.sleep(0.01)
    old = time.time() - 40 * 24 * 3600
    for path in store.history():
        os.utime(path, (old, old))
        os.utime(path.with_suffix(".txt"), (old, old))
    store.save(_report(), retention=Retention(keep=1, days=7))
    assert len(store.history()) == 1
    assert len(list((tmp_path / "reports").glob("*.txt"))) == 1


def test_baseline_ref_and_threshold_survive_round_trip(tmp_path: Path) -> None:
    store = ReportStore(tmp_path / "reports")
    result = AnalysisResult(baseline_ref="bl_42", threshold=Severity.WARNING)
    store.save(Report.from_run(result, app="app:app", project_root="/tmp"))
    restored = store.latest()
    assert restored is not None
    assert restored.result.baseline_ref == "bl_42"
    assert restored.result.threshold is Severity.WARNING
