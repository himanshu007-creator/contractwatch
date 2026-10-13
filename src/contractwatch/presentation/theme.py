"""Terminal styling tokens shared by every renderer."""

from __future__ import annotations

from ..diagnostics import Severity

SEVERITY_STYLE = {
    Severity.ERROR: "bold red",
    Severity.WARNING: "bold yellow",
    Severity.INFO: "cyan",
}

SEVERITY_GLYPH = {
    Severity.ERROR: "✗",
    Severity.WARNING: "!",
    Severity.INFO: "•",
}

SEVERITY_GLYPH_ASCII = {
    Severity.ERROR: "FAIL",
    Severity.WARNING: "WARN",
    Severity.INFO: "INFO",
}

BANNER = "ContractWatch"
FEATURE = "bold"
MUTED = "dim"
CODE = "magenta"
CHECK = "bold green"
