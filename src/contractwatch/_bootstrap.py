"""Child-process entry point: import the app, emit a JSON payload on stdout.

Invoked as ``python -m contractwatch._bootstrap <module:attr> [sys_path ...]``.
Kept tiny so a crash here can never damage the parent process or project files.
"""

from __future__ import annotations

import json
import sys

from .loader import build_payload


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(json.dumps({"ok": False, "category": "AppLoadError", "message": "Missing target."}))
        return 0
    target, *sys_paths = args
    payload = build_payload(target, sys_paths)
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
