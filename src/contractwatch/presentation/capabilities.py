"""Runtime rendering capabilities: colour, animation, and terminal width."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

_NO_COLOR = ("NO_COLOR", "CONTRACTWATCH_NO_COLOR")
_NO_ANIMATION = ("CONTRACTWATCH_NO_ANIMATION", "CI", "GITHUB_ACTIONS")
_ASCII = "CONTRACTWATCH_ASCII"


@dataclass(frozen=True, slots=True)
class Capabilities:
    color: bool
    animation: bool
    unicode: bool
    width: int

    @classmethod
    def detect(
        cls,
        *,
        no_color: bool = False,
        no_animation: bool = False,
        stream: object | None = None,
    ) -> Capabilities:
        stream = stream if stream is not None else sys.stdout
        is_tty = bool(getattr(stream, "isatty", lambda: False)())
        environ = os.environ
        color = is_tty and "TERM" not in {"dumb"} and all(environ.get(k) is None for k in _NO_COLOR)
        animation = color and not no_animation and all(environ.get(k) is None for k in _NO_ANIMATION)
        unicode = environ.get(_ASCII) is None and _utf8_compatible(stream)
        width = _terminal_width(stream)
        if no_color:
            color = False
            animation = False
        return cls(color=color, animation=animation, unicode=unicode, width=width)


def _utf8_compatible(stream: object) -> bool:
    try:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        return "utf" in encoding.lower() or "ascii" not in encoding.lower()
    except Exception:
        return True


def _terminal_width(stream: object) -> int:
    try:
        return shutil.get_terminal_size(fallback=(100, 24)).columns
    except Exception:
        return 100


__all__ = ["Capabilities"]
