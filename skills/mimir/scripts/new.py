#!/usr/bin/env python3
"""Start a note of a configured kind from its template.

A kind with ``hub`` gets its own folder, ``<area>/<folder>/<name>/<name>.md``;
any other kind is ``<area>/<folder>/<name>.md``. ``{{name}}`` and ``{{date}}``
in the template are filled. The note is created the way split.py does it.

Usage: new.py KIND NAME --area AREA [--parent NAME] [--vault PATH]
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import obsidian_cli  # noqa: E402
import scaffold  # noqa: E402
from split import _markdown_files  # noqa: E402


class NewError(Exception):
    """The note cannot be created as asked."""


class NameTaken(NewError):
    def __init__(self, path):
        super().__init__(f"a note called {Path(path).stem} exists already: "
                         f"{path}")
        self.path = path


class UnknownArea(NewError):
    pass


class UnknownKind(NewError):
    pass


_BAD_NAME = re.compile(r'[/\\:#^|\[\]*"<>?]')


def _with_parent(text: str, parent: str) -> str:
    """text with ``project: "[[parent]]"`` set in its frontmatter."""
    line = f'project: "[[{parent}]]"'
    lines = text.split("\n")
    if lines[0].rstrip("\r") != "---":
        return text
    for end in range(1, len(lines)):
        if lines[end].rstrip("\r") == "---":
            break
    else:
        return text
    for index in range(1, end):
        if lines[index].startswith("project:"):
            lines[index] = line
            return "\n".join(lines)
    lines.insert(end, line)
    return "\n".join(lines)


def new_note(vault: Path, cfg: config.Config, kind: str, name: str,
             area: str, parent: str | None = None) -> Path:
    vault = Path(vault)
    if kind not in cfg.kinds:
        raise UnknownKind(f"unknown kind {kind}; known: "
                          f"{', '.join(cfg.kinds)}")
    if area not in cfg.areas:
        raise UnknownArea(f"unknown area {area}; known: "
                          f"{', '.join(cfg.areas)}")
    if not name.strip() or _BAD_NAME.search(name):
        raise NewError(f"{name!r} cannot be a note name")
    spec = cfg.kinds[kind]
    for existing in _markdown_files(vault):
        if existing.name[:-3].casefold() == name.casefold():
            raise NameTaken(existing.relative_to(vault).as_posix())
    template = vault / spec.template if spec.template else None
    if template is not None and template.is_file():
        text = template.read_text(encoding="utf-8")
    else:
        text = scaffold.template_text(spec)
    text = text.replace("{{name}}", name).replace(
        "{{date}}", datetime.date.today().isoformat())
    if parent:
        text = _with_parent(text, parent)
    parts = [area] + ([spec.folder] if spec.folder else [])
    if spec.hub:
        parts.append(name)
    rel = "/".join(parts + [f"{name}.md"])
    try:
        obsidian_cli.run(["version"])
        return obsidian_cli.create_note(vault, rel, text)
    except obsidian_cli.CliError as err:
        raise NewError(str(err)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="new.py", description="Start a note of a configured kind.")
    parser.add_argument("kind")
    parser.add_argument("name")
    parser.add_argument("--area", required=True)
    parser.add_argument("--parent", help="the project this note belongs to")
    parser.add_argument("--vault", default=".", help="the vault (default: .)")
    args = parser.parse_args(argv)
    vault = Path(args.vault).resolve()
    try:
        cfg = config.load(vault)
        path = new_note(vault, cfg, args.kind, args.name, args.area,
                        args.parent)
    except config.ConfigError as err:
        print(f"new: {err}", file=sys.stderr)
        return 2
    except NewError as err:
        print(f"new: {err}", file=sys.stderr)
        return 1
    print(path.relative_to(vault).as_posix())
    return 0


if __name__ == "__main__":
    sys.exit(main())
