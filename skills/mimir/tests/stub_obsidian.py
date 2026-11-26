#!/usr/bin/env python3
"""A stand-in for the Obsidian CLI, selected in tests through MIMIR_OBSIDIAN.

Every call appends its arguments as one JSON line to MIMIR_STUB_LOG. When the
command (the first argument) is named in MIMIR_STUB_ERROR, the stub prints an
``Error:`` line and exits 0, as the real CLI does; when it is named in
MIMIR_STUB_EXIT, it exits 1. A ``create path=...`` call makes an empty file
under MIMIR_STUB_VAULT, when that is set.
"""

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    log = os.environ.get("MIMIR_STUB_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv, ensure_ascii=False) + "\n")
    command = argv[0] if argv else ""
    if command in os.environ.get("MIMIR_STUB_ERROR", "").split(","):
        print(f'Error: stub refuses "{command}"')
        return 0
    if command in os.environ.get("MIMIR_STUB_EXIT", "").split(","):
        print(f"stub exits 1 on {command}", file=sys.stderr)
        return 1
    if command == "version":
        print("1.12.0 (stub)")
        return 0
    if command == "create":
        vault = os.environ.get("MIMIR_STUB_VAULT")
        args = dict(arg.split("=", 1) for arg in argv[1:] if "=" in arg)
        if vault and "path" in args:
            path = Path(vault) / args["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        print(f"Created: {args.get('path', '')}")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
