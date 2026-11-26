#!/usr/bin/env python3
"""Split a long note into a hub and parts, moving its text, never rewriting it.

The hub keeps the note's path, its frontmatter and the text above the first
section, followed by a ``## Parts`` list that links each part. Each part is
one section, from its heading to the next heading of the same level, and is
named ``<note> – <heading>``. Headings inside fenced code are text, not
sections. Links into the note's sections (``[[Note#Heading|shown]]``) are
rewritten to the part; plain links keep resolving to the hub.

Usage: split.py NOTE [--vault PATH] [--level N] [--dry-run] [--force]
"""

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import obsidian_cli  # noqa: E402
from notes import parse_front  # noqa: E402

PARTS_HEADING = "## Parts\n"
DASH = "–"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING = re.compile(r"(?:^|[ \t]+)#+$")
# Characters that cannot stand in a note name or a wikilink target.
_FLATTEN = re.compile(r'[/:\\#^|\[\]*"<>?]')
_LINK = re.compile(r"\[\[([^\[\]|#\n]*)#([^\[\]|\n]*)((?:\|[^\[\]\n]*)?)\]\]")


class SplitError(Exception):
    """The note cannot be split as asked."""


@dataclass(frozen=True)
class Heading:
    offset: int
    depth: int
    text: str


def _lines_with_fence(lines: list[str]):
    """Yield (line, fenced) for each line; fence delimiters count as fenced."""
    opener = None
    for line in lines:
        bare = line.rstrip("\r\n")
        match = _FENCE.match(bare)
        if opener is None:
            if match:
                opener = match.group(1)
                yield line, True
            else:
                yield line, False
        else:
            if match and match.group(1)[0] == opener[0] \
                    and len(match.group(1)) >= len(opener) \
                    and not bare[match.end():].strip():
                opener = None
            yield line, True


def _body_start(lines: list[str]) -> int:
    """The index of the first line after the frontmatter, as parse_front reads it."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return 0
    for end in range(1, len(lines)):
        if lines[end].rstrip("\r\n").rstrip() == "---":
            return end + 1
    return 0


def _headings(text: str) -> list[Heading]:
    lines = text.splitlines(keepends=True)
    start = _body_start(lines)
    offset = sum(len(line) for line in lines[:start])
    found = []
    for line, fenced in _lines_with_fence(lines[start:]):
        match = None if fenced else _HEADING.match(line.rstrip("\r\n"))
        if match:
            title = _CLOSING.sub("", match.group(2) or "").strip()
            if title:
                found.append(Heading(offset, len(match.group(1)), title))
        offset += len(line)
    return found


def top_level(text: str) -> int:
    """The lowest heading depth that occurs more than once outside fences."""
    counts = {}
    for heading in _headings(text):
        counts[heading.depth] = counts.get(heading.depth, 0) + 1
    repeated = [depth for depth, count in counts.items() if count > 1]
    if not repeated:
        raise SplitError("no heading level occurs more than once")
    return min(repeated)


def part_name(name: str, heading: str) -> str:
    """``<name> – <heading>``, with characters a note name cannot hold flattened."""
    flat = " ".join(_FLATTEN.sub(" ", heading).split())
    return f"{name} {DASH} {flat}"


def _plan(name: str, text: str, level: int):
    """(hub, parts, sections) where sections maps a heading to its link target."""
    if level == 0:
        level = top_level(text)
    if not 1 <= level <= 6:
        raise SplitError(f"level {level}: must be 1 to 6")
    heads = _headings(text)
    cuts = [h for h in heads if h.depth == level]
    if not cuts:
        raise SplitError(f"no heading of level {level} outside fenced code")
    parts, used, owner = [], set(), []
    for index, cut in enumerate(cuts):
        end = cuts[index + 1].offset if index + 1 < len(cuts) else len(text)
        base = part_name(name, cut.text)
        if base == f"{name} {DASH} ":
            base += f"Part {index + 1}"
        unique, n = base, 1
        while unique.casefold() in used:
            n += 1
            unique = f"{base} {n}"
        used.add(unique.casefold())
        parts.append((unique, text[cut.offset:end]))
        owner.append((cut.offset, end, unique))
    sections = {}
    for cut, (_, _, base) in zip(cuts, owner):
        sections.setdefault(cut.text, base)
    for head in heads:
        for start, end, base in owner:
            if start < head.offset < end and head.depth > level:
                sections.setdefault(head.text, f"{base}#{head.text}")
    hub = text[:cuts[0].offset] + PARTS_HEADING + "\n" + "".join(
        f"- [[{base}]]\n" for base, _ in parts)
    return hub, parts, sections


def split_text(name: str, text: str,
               level: int = 0) -> tuple[str, list[tuple[str, str]]]:
    """The hub text and the (part basename, part text) pairs of a note."""
    hub, parts, _ = _plan(name, text, level)
    return hub, parts


def join_parts(hub: str, parts: list[tuple[str, str]]) -> str:
    """The original text of a note that split_text turned into hub and parts."""
    cut = hub.rfind(PARTS_HEADING)
    if cut == -1 or (cut > 0 and hub[cut - 1] != "\n"):
        raise SplitError("the hub has no ## Parts list")
    return hub[:cut] + "".join(text for _, text in parts)


def _resolve_anchor(anchor: str, sections: dict[str, str]) -> str | None:
    segments = [segment.strip() for segment in anchor.split("#")]
    if segments[0] in sections:
        return sections[segments[0]] + "".join(f"#{s}" for s in segments[1:])
    if len(segments) > 1 and segments[-1] in sections:
        return sections[segments[-1]]
    return None


def _text_rewrites(text: str, stem: str, name: str,
                   sections: dict[str, str]) -> list[tuple[str, str]]:
    """The (old, new) links in one note's text, outside fenced code."""
    own = {name} | {target.split("#", 1)[0] for target in sections.values()}
    found = []
    for line, fenced in _lines_with_fence(text.splitlines(keepends=True)):
        if fenced:
            continue
        for match in _LINK.finditer(line):
            target, anchor, alias = match.groups()
            folder, slash, last = target.strip().rpartition("/")
            base = last[:-3] if last.endswith(".md") else last
            if target.strip():
                if base != name:
                    continue
            elif stem not in own:
                continue
            new_target = _resolve_anchor(anchor, sections)
            if new_target is None:
                continue
            if not target.strip():
                if new_target.split("#", 1)[0] == stem:
                    continue
            elif slash:
                new_target = f"{folder}/{new_target}"
            pair = (match.group(0), f"[[{new_target}{alias}]]")
            if pair not in found:
                found.append(pair)
    return found


def _markdown_files(vault: Path):
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for file in sorted(files):
            if file.endswith(".md"):
                yield Path(root) / file


def heading_link_rewrites(vault: Path, name: str,
                          sections: dict[str, str]) -> list[tuple[Path, str, str]]:
    """Every (file, old link, new link) that points into a split section.

    sections maps a heading of the note called name to its new link target,
    a part's basename, or ``part#subheading`` for a heading inside a part.
    """
    found = []
    for path in _markdown_files(Path(vault)):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for old, new in _text_rewrites(text, path.name[:-3], name, sections):
            found.append((path, old, new))
    return found


def apply_rewrites(rewrites: list[tuple[Path, str, str]]) -> None:
    """Edit each file, replacing old links with new ones outside fenced code."""
    by_file = {}
    for path, old, new in rewrites:
        by_file.setdefault(path, []).append((old, new))
    for path, pairs in by_file.items():
        text = path.read_text(encoding="utf-8")
        out = []
        for line, fenced in _lines_with_fence(text.splitlines(keepends=True)):
            if not fenced:
                for old, new in pairs:
                    line = line.replace(old, new)
            out.append(line)
        path.write_bytes("".join(out).encode("utf-8"))


def skip_reason(cfg: config.Config, rel: str, front: dict | None) -> str | None:
    """Why the config keeps this note out of the cap and the splitter, if it does."""
    for folder in cfg.size_skip_folders:
        folder = folder.strip("/")
        if rel == folder or rel.startswith(folder + "/"):
            return f"folder {folder}"
    file = rel.rsplit("/", 1)[-1]
    stem = file[:-3] if file.endswith(".md") else file
    for pattern in cfg.size_skip_names:
        if fnmatch.fnmatchcase(stem, pattern) or fnmatch.fnmatchcase(file, pattern):
            return f"name {pattern}"
    note_type = front.get("type") if front else None
    if isinstance(note_type, str) and note_type in cfg.size_skip_types:
        return f"type {note_type}"
    return None


def _find_note(vault: Path, note: str) -> Path:
    given = Path(note)
    for candidate in (given, vault / note, vault / f"{note}.md"):
        if candidate.is_file():
            path = candidate.resolve()
            if path.is_relative_to(vault):
                return path
    stem = note[:-3] if note.endswith(".md") else note
    matches = [p for p in _markdown_files(vault) if p.name[:-3] == stem]
    if len(matches) == 1:
        return matches[0].resolve()
    if matches:
        raise SplitError(f"{note}: {len(matches)} notes have this name; "
                         "give the path")
    raise SplitError(f"{note}: no such note in {vault}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="split.py", description="Split a note into a hub and parts.")
    parser.add_argument("note", help="the note's path or name")
    parser.add_argument("--vault", default=".", help="the vault (default: .)")
    parser.add_argument("--level", type=int, default=0,
                        help="the heading depth to split at (default: the "
                             "lowest depth that occurs more than once)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print what would change and write nothing")
    parser.add_argument("--force", action="store_true",
                        help="split a note that is not over the size cap")
    args = parser.parse_args(argv)

    vault = Path(args.vault).resolve()
    try:
        cfg = config.load(vault)
        path = _find_note(vault, args.note)
        rel = path.relative_to(vault).as_posix()
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise SplitError(f"{rel}: not UTF-8") from None
        front, _ = parse_front(text)
        reason = skip_reason(cfg, rel, front)
        if reason:
            raise SplitError(f"{rel}: skipped by the config ({reason})")
        if len(data) <= cfg.size_cap_bytes and not args.force:
            raise SplitError(
                f"{rel}: {len(data)} bytes, not over the cap of "
                f"{cfg.size_cap_bytes}; use --force to split it anyway")
        name = path.name[:-3]
        hub, parts, sections = _plan(name, text, args.level)
        taken = {p.name[:-3].casefold() for p in _markdown_files(vault)}
        clash = [base for base, _ in parts if base.casefold() in taken]
        if clash:
            raise SplitError("a note with this name exists already: "
                             + ", ".join(clash))
    except (config.ConfigError, SplitError) as err:
        print(f"split: {err}", file=sys.stderr)
        return 1

    folder = path.parent.relative_to(vault).as_posix()
    prefix = "" if folder == "." else f"{folder}/"

    if args.dry_run:
        for base, part in parts:
            print(f"would create {prefix}{base}.md "
                  f"({len(part.encode('utf-8'))} bytes)")
        print(f"would write the hub {rel} ({len(hub.encode('utf-8'))} bytes)")
        planned = []
        for file, old, new in heading_link_rewrites(vault, name, sections):
            if file != path:
                planned.append((file.relative_to(vault).as_posix(), old, new))
        for base, part in [(name, hub), *parts]:
            for old, new in _text_rewrites(part, base, name, sections):
                planned.append((f"{prefix}{base}.md", old, new))
        for file, old, new in planned:
            print(f"would rewrite {file}: {old} -> {new}")
        return 0

    created = []
    try:
        obsidian_cli.run(["version"])
    except obsidian_cli.CliError as err:
        print(f"split: the Obsidian app does not answer: {err}",
              file=sys.stderr)
        return 1
    for base, part in parts:
        part_rel = f"{prefix}{base}.md"
        try:
            obsidian_cli.run(["create", f"path={part_rel}"])
        except obsidian_cli.CliError as err:
            print(f"split: {err}", file=sys.stderr)
            print(f"split: stopped; {rel} is untouched; created before the "
                  f"failure: {', '.join(created) or 'nothing'}",
                  file=sys.stderr)
            return 1
        # content= would turn every backslash-n in the text into a newline,
        # so the part's exact bytes go to the file the app has created.
        (vault / part_rel).write_bytes(part.encode("utf-8"))
        created.append(part_rel)
        print(f"created {part_rel}")
    path.write_bytes(hub.encode("utf-8"))
    print(f"wrote the hub {rel}")
    rewrites = heading_link_rewrites(vault, name, sections)
    apply_rewrites(rewrites)
    for file, old, new in rewrites:
        print(f"rewrote {file.relative_to(vault).as_posix()}: {old} -> {new}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
