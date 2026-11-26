#!/usr/bin/env python3
"""Create the note templates the config names, never touching an existing one.

A template carries the kind's ``type``, ``status: planned`` when the kind has
a status, and each required key empty.

Usage: scaffold.py [--vault PATH] [--dry-run]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402


def template_text(kind: config.Kind) -> str:
    """The text of a kind's template, with {{title}} and {{date}} left in, which Obsidian's Templates plugin and new.py both fill."""
    lines = ["---", f"type: {kind.note_type}"]
    if kind.has_status:
        lines.append("status: planned")
    for key in kind.required:
        if key not in ("type", "status"):
            lines.append(f"{key}:")
    lines += ["---", "", "# {{title}}", ""]
    return "\n".join(lines)


def scaffold(vault: Path, cfg: config.Config,
             dry_run: bool = False) -> list[Path]:
    """Create each missing template; the paths created (or that would be)."""
    made = []
    for kind in cfg.kinds.values():
        if not kind.template:
            continue
        path = Path(vault) / kind.template
        if path.exists():
            continue
        made.append(path)
        if not dry_run:
            os.makedirs(path.parent, exist_ok=True)
            path.write_bytes(template_text(kind).encode("utf-8"))
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scaffold.py", description="Create missing note templates.")
    parser.add_argument("--vault", default=".", help="the vault (default: .)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would be created and write nothing")
    args = parser.parse_args(argv)
    vault = Path(args.vault).resolve()
    try:
        cfg = config.load(vault)
    except config.ConfigError as err:
        print(f"scaffold: {err}", file=sys.stderr)
        return 2
    verb = "would create" if args.dry_run else "created"
    for path in scaffold(vault, cfg, args.dry_run):
        print(f"{verb} {path.relative_to(vault).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
