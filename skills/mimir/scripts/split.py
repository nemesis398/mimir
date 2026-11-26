#!/usr/bin/env python3
"""Split a long note into a hub and parts, moving its text, never rewriting it.

Every split note lives in its own folder: a note that is not at
``<dir>/<name>/<name>.md`` yet is moved there through the app first, which
keeps plain links resolving. The hub keeps the note's frontmatter, the text
above the first section and the leading sections named in ``keep``, followed
by a ``## Parts`` list that links each part. Each part is one section, from
its heading to the next heading of the same level, and is named
``<note> – <heading>``, next to the hub. For a plan the default ``keep`` is
every section before the first ``Phase `` or ``Task `` heading. Headings
inside fenced code are text, not sections. Links into the note's sections
and blocks (``[[Note#Heading|shown]]``, ``![[Note#^block]]``,
``[shown](Note.md#Heading)``) are rewritten to the part; headings match
without regard to case, as in Obsidian. Every other link into the note is
printed, so nothing changes unseen.

Usage: split.py NOTE [--vault PATH] [--level N] [--keep HEADING]...
                [--dry-run] [--force]
"""

import argparse
import contextlib
import fnmatch
import os
import re
import stat
import sys
import tempfile
from urllib.parse import unquote
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import obsidian_cli  # noqa: E402
from notes import front_end, parse_front  # noqa: E402

PARTS_HEADING = "## Parts\n"
DASH = "–"
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
_CLOSING = re.compile(r"(?:^|[ \t]+)#+$")
# Characters that cannot stand in a note name or a wikilink target.
_FLATTEN = re.compile(r'[/:\\#^|\[\]*"<>?]')
_WIKI = re.compile(
    r"(!?)\[\[([^\[\]|#\n]*)((?:#[^\[\]|\n]*)?)((?:\|[^\[\]\n]*)?)\]\]")
_MARKDOWN = re.compile(
    r'(!?\[[^\[\]\n]*\])\((<[^<>\n]*>|[^()<>\s]+)((?:[ \t]+"[^"\n]*")?)\)')
_BLOCK = re.compile(r"(?:^|\s)\^([A-Za-z0-9-]+)[ \t]*$")


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
    end = front_end(lines)
    return 0 if end is None else end + 1


def _body_lines(text: str):
    """Yield (offset, line without its ending) for each body line outside fences."""
    lines = text.splitlines(keepends=True)
    start = _body_start(lines)
    offset = sum(len(line) for line in lines[:start])
    for line, fenced in _lines_with_fence(lines[start:]):
        if not fenced:
            yield offset, line.rstrip("\r\n")
        offset += len(line)


def _headings(text: str) -> list[Heading]:
    found = []
    for offset, line in _body_lines(text):
        match = _HEADING.match(line)
        if match:
            title = _CLOSING.sub("", match.group(2) or "").strip()
            if title:
                found.append(Heading(offset, len(match.group(1)), title))
    return found


def _blocks(text: str) -> list[tuple[int, str]]:
    """(offset, ``^id``) of each block id outside fences."""
    found = []
    for offset, line in _body_lines(text):
        match = None if _HEADING.match(line) else _BLOCK.search(line)
        if match:
            found.append((offset, f"^{match.group(1)}"))
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


PLAN_STARTS = ("Phase ", "Task ")


def plan_part_front(cfg: config.Config, status) -> str:
    """The frontmatter of a plan's part: the part type and the hub's status."""
    kind = cfg.kinds.get("plan-part")
    lines = [f"type: {kind.note_type if kind else 'plan-part'}"]
    if status:
        lines.append(f"status: {status}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def plan_keep(text: str, level: int = 0) -> tuple[str, ...]:
    """The headings at level before the first one that starts a phase or task.

    These are a plan's leading sections (constraints, review focus), which
    stay in the hub. A plan with no phase or task heading keeps none.
    """
    if level == 0:
        level = top_level(text)
    keep = []
    for head in _headings(text):
        if head.depth != level:
            continue
        if head.text.startswith(PLAN_STARTS):
            return tuple(keep)
        keep.append(head.text)
    return ()


def _plan(name: str, text: str, level: int, keep=()):
    """(hub, parts, sections) where sections maps a heading to its link target.

    A block id is a key too, as ``^id``. A heading or block that stays in the
    hub maps to ``name#heading``; the first of two equal headings wins, as
    in Obsidian.
    """
    if level == 0:
        level = top_level(text)
    if not 1 <= level <= 6:
        raise SplitError(f"level {level}: must be 1 to 6")
    heads = _headings(text)
    at_level = [h for h in heads if h.depth == level]
    if not at_level:
        raise SplitError(f"no heading of level {level} outside fenced code")
    # From the first heading at level on, a shallower heading starts a part
    # too, so a section never runs on past a heading above it.
    cuts = [h for h in heads
            if h.offset >= at_level[0].offset and h.depth <= level]
    keep = set(keep)
    missing = sorted(keep - {cut.text for cut in cuts})
    if missing:
        raise SplitError(f"keep {missing[0]!r}: no heading of level {level} "
                         "has this text")
    kept = 0
    while kept < len(cuts) and cuts[kept].text in keep:
        kept += 1
    if kept == len(cuts):
        raise SplitError("every section is kept; nothing is left to split")
    late = [cut.text for cut in cuts[kept:] if cut.text in keep]
    if late:
        raise SplitError(f"keep {late[0]!r}: only the sections before the "
                         "first part can stay in the hub")
    cuts = cuts[kept:]
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
    anchors = sorted([(head.offset, head.text) for head in heads]
                     + _blocks(text))
    sections = {}
    for offset, key in anchors:
        target = f"{name}#{key}"
        for start, end, base in owner:
            if start <= offset < end:
                heads_part = offset == start and not key.startswith("^")
                target = base if heads_part else f"{base}#{key}"
        sections.setdefault(key, target)
    hub = text[:cuts[0].offset] + PARTS_HEADING + "\n" + "".join(
        f"- [[{base}]]\n" for base, _ in parts)
    return hub, parts, sections


def split_text(name: str, text: str, level: int = 0,
               keep=()) -> tuple[str, list[tuple[str, str]]]:
    """The hub text and the (part basename, part text) pairs of a note.

    The sections whose heading text is in keep stay in the hub; they must
    come before every section that becomes a part.
    """
    hub, parts, _ = _plan(name, text, level, keep)
    return hub, parts


def join_parts(hub: str, parts: list[tuple[str, str]]) -> str:
    """The original text of a note that split_text turned into hub and parts."""
    cut = hub.rfind(PARTS_HEADING)
    if cut == -1 or (cut > 0 and hub[cut - 1] != "\n"):
        raise SplitError("the hub has no ## Parts list")
    return hub[:cut] + "".join(text for _, text in parts)


def _resolve_anchor(anchor: str, folded: dict[str, str]) -> str | None:
    segments = [segment.strip().casefold() for segment in anchor.split("#")]
    if segments[0] in folded:
        rest = [segment.strip() for segment in anchor.split("#")[1:]]
        return folded[segments[0]] + "".join(f"#{s}" for s in rest)
    if len(segments) > 1 and segments[-1] in folded:
        return folded[segments[-1]]
    return None


def _link_target(target: str, anchor: str | None, stem: str, name: str,
                 own: set[str], folded: dict[str, str]):
    """("new", target) for a link to rewrite, ("left", reason) for a link
    into the note that stays, None for a link elsewhere or one that still
    resolves in its own file."""
    folder, slash, last = target.rpartition("/")
    base = last[:-3] if last.casefold().endswith(".md") else last
    if target:
        if base.casefold() != name.casefold():
            return None
    elif stem.casefold() not in own:
        return None
    if anchor is None:
        return ("left", "links the hub") if target else None
    new = _resolve_anchor(anchor, folded)
    if new is None:
        return "left", "no such heading or block"
    new_base = new.split("#", 1)[0].casefold()
    if not target:
        return None if new_base == stem.casefold() else ("new", new)
    if new_base == name.casefold():
        return "left", "stays in the hub"
    return "new", f"{folder}/{new}" if slash else new


def _md_quote(text: str) -> str:
    """text fit for a Markdown link destination, with the fewest escapes."""
    for char, code in (("%", "%25"), (" ", "%20"), ("(", "%28"),
                       (")", "%29"), ("<", "%3C"), (">", "%3E")):
        text = text.replace(char, code)
    return text


def _scan_text(text: str, stem: str, name: str, sections: dict[str, str]):
    """(rewrites, left) for one note's text, outside fenced code.

    rewrites holds the (old, new) links; left holds (link, reason) for each
    link into the note that is not rewritten.
    """
    folded = {}
    for key, target in sections.items():
        folded.setdefault(key.casefold(), target)
    own = {name.casefold()} | {target.split("#", 1)[0].casefold()
                               for target in sections.values()}
    rewrites, left = [], []

    def add(item, into):
        if item not in into:
            into.append(item)

    for line, fenced in _lines_with_fence(text.splitlines(keepends=True)):
        if fenced:
            continue
        for match in _WIKI.finditer(line):
            bang, target, anchor, alias = match.groups()
            old = match.group(0)[len(bang):]
            found = _link_target(target.strip(), anchor[1:] if anchor else None,
                                 stem, name, own, folded)
            if found is None:
                continue
            if found[0] == "new":
                add((old, f"[[{found[1]}{alias}]]"), rewrites)
            elif bang and found[1] == "links the hub":
                add((bang + old, "embeds the hub, not the parts"), left)
            else:
                add((bang + old, found[1]), left)
        for match in _MARKDOWN.finditer(line):
            shown, dest, title = match.groups()
            angle = dest.startswith("<")
            raw = dest[1:-1] if angle else dest
            if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", raw):
                continue
            path, hashed, anchor = unquote(raw).partition("#")
            found = _link_target(path.strip(), anchor if hashed else None,
                                 stem, name, own, folded)
            if found is None:
                continue
            if found[0] == "left":
                add((match.group(0), found[1]), left)
                continue
            new_path, hashed, new_anchor = found[1].partition("#")
            if not path or path.casefold().endswith(".md"):
                new_path += ".md"
            if angle:
                new_dest = f"<{new_path}{hashed}{new_anchor}>"
            else:
                new_dest = _md_quote(new_path) + hashed + _md_quote(new_anchor)
            add((match.group(0), f"{shown}({new_dest}{title})"), rewrites)
    return rewrites, left


def _text_rewrites(text: str, stem: str, name: str,
                   sections: dict[str, str]) -> list[tuple[str, str]]:
    """The (old, new) links in one note's text, outside fenced code."""
    return _scan_text(text, stem, name, sections)[0]


def _markdown_files(vault: Path):
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for file in sorted(files):
            if file.endswith(".md"):
                yield Path(root) / file


def link_scan(vault: Path, name: str, sections: dict[str, str]):
    """(rewrites, left) over the vault: (file, old, new) for each link to
    rewrite and (file, link, reason) for each link into the note that stays.
    """
    rewrites, left = [], []
    for path in _markdown_files(Path(vault)):
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found, stays = _scan_text(text, path.name[:-3], name, sections)
        rewrites += [(path, old, new) for old, new in found]
        left += [(path, link, reason) for link, reason in stays]
    return rewrites, left


def heading_link_rewrites(vault: Path, name: str,
                          sections: dict[str, str]) -> list[tuple[Path, str, str]]:
    """Every (file, old link, new link) that points into a split section.

    sections maps a heading of the note called name to its new link target,
    a part's basename, or ``part#subheading`` for a heading inside a part;
    ``^id`` keys are block ids. Headings match without regard to case.
    """
    return link_scan(vault, name, sections)[0]


def write_atomic(path: Path, data: bytes) -> None:
    """Write data to path through a temp file next to it and os.replace.

    A reader sees the old file or the new one, never half of it. A symlink
    is followed, so the file it points at is replaced and the link stays;
    the file keeps its mode.
    """
    real = Path(os.path.realpath(path))
    fd, tmp = tempfile.mkstemp(dir=real.parent, prefix=f".{real.name}.",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with contextlib.suppress(FileNotFoundError):
            os.chmod(tmp, stat.S_IMODE(os.stat(real).st_mode))
        os.replace(tmp, real)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def apply_rewrites(rewrites: list[tuple[Path, str, str]]) -> None:
    """Edit each file, replacing old links with new ones outside fenced code.

    Each file is read when it is edited and written with write_atomic.
    """
    by_file = {}
    for path, old, new in rewrites:
        by_file.setdefault(path, []).append((old, new))
    for path, pairs in by_file.items():
        text = path.read_bytes().decode("utf-8")
        out = []
        for line, fenced in _lines_with_fence(text.splitlines(keepends=True)):
            if not fenced:
                for old, new in pairs:
                    line = line.replace(old, new)
            out.append(line)
        new_text = "".join(out)
        if new_text != text:
            write_atomic(path, new_text.encode("utf-8"))


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


def _not_a_symlink(path: Path, note: str) -> Path:
    """path resolved; SplitError when the note itself is a symlink."""
    if path.is_symlink():
        raise SplitError(f"{note}: a symlink to {os.path.realpath(path)}; "
                         "split does not follow it, split that note instead")
    return path.resolve()


def _find_note(vault: Path, note: str) -> Path:
    given = Path(note)
    for candidate in (given, vault / note, vault / f"{note}.md"):
        if candidate.is_file():
            path = candidate.resolve()
            if path.is_relative_to(vault):
                return _not_a_symlink(candidate, note)
    stem = note[:-3] if note.endswith(".md") else note
    matches = [p for p in _markdown_files(vault) if p.name[:-3] == stem]
    if len(matches) == 1:
        return _not_a_symlink(matches[0], note)
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
    parser.add_argument("--keep", action="append", metavar="HEADING",
                        help="a leading section that stays in the hub; "
                             "repeatable (default for a plan: every section "
                             "before the first Phase or Task heading)")
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
        status = front.get("status") if front else None
        plan_type = cfg.kinds["plan"].note_type if "plan" in cfg.kinds \
            else "plan"
        is_plan = bool(front) and front.get("type") == plan_type
        if status == "active" and is_plan:
            raise SplitError(f"{rel}: status is active; a plan that is "
                             "being worked is not split")
        if len(data) <= cfg.size_cap_bytes and not args.force:
            raise SplitError(
                f"{rel}: {len(data)} bytes, not over the cap of "
                f"{cfg.size_cap_bytes}; use --force to split it anyway")
        name = path.name[:-3]
        if args.keep is not None:
            keep = tuple(args.keep)
        elif is_plan:
            keep = plan_keep(text, args.level)
        else:
            keep = ()
        hub, parts, sections = _plan(name, text, args.level, keep)
        part_type = cfg.kinds["plan-part"].note_type \
            if "plan-part" in cfg.kinds else "plan-part"
        if is_plan or (front and front.get("type") == part_type):
            parts = [(base, plan_part_front(cfg, status) + part)
                     for base, part in parts]
        taken = {p.name[:-3].casefold() for p in _markdown_files(vault)}
        clash = [base for base, _ in parts if base.casefold() in taken]
        if clash:
            raise SplitError("a note with this name exists already: "
                             + ", ".join(clash))
        if path.parent != vault and path.parent.name == name:
            home = path.parent.relative_to(vault).as_posix()
        else:
            folder = path.parent.relative_to(vault).as_posix()
            home = name if folder == "." else f"{folder}/{name}"
            if (vault / home / f"{name}.md").exists():
                raise SplitError(f"{home}/{name}.md exists already")
    except (config.ConfigError, SplitError) as err:
        print(f"split: {err}", file=sys.stderr)
        return 1

    prefix = f"{home}/"
    new_rel = f"{prefix}{name}.md"
    moving = new_rel != rel

    if args.dry_run:
        if moving:
            print(f"would move {rel} to {new_rel}")
        for base, part in parts:
            print(f"would create {prefix}{base}.md "
                  f"({len(part.encode('utf-8'))} bytes)")
        print(f"would write the hub {new_rel} "
              f"({len(hub.encode('utf-8'))} bytes)")
        planned, left = [], []
        found, stays = link_scan(vault, name, sections)
        for file, old, new in found:
            if file != path:
                planned.append((file.relative_to(vault).as_posix(), old, new))
        for file, link, reason in stays:
            if file != path:
                left.append((file.relative_to(vault).as_posix(), link, reason))
        for base, part in [(name, hub), *parts]:
            found, stays = _scan_text(part, base, name, sections)
            planned += [(f"{prefix}{base}.md", old, new) for old, new in found]
            left += [(f"{prefix}{base}.md", link, reason)
                     for link, reason in stays]
        for file, old, new in planned:
            print(f"would rewrite {file}: {old} -> {new}")
        for file, link, reason in left:
            print(f"would leave {file}: {link} ({reason})")
        return 0

    created = []
    try:
        obsidian_cli.run(["version"], cwd=vault)
        obsidian_cli.check_vault(vault)
    except obsidian_cli.CliError as err:
        print(f"split: the Obsidian app does not answer: {err}",
              file=sys.stderr)
        return 1
    except obsidian_cli.WrongVault as err:
        print(f"split: {err}", file=sys.stderr)
        return 2
    if moving:
        target = vault / new_rel
        try:
            os.makedirs(target.parent, exist_ok=True)
            obsidian_cli.run(["move", f"path={rel}", f"to={new_rel}"],
                             cwd=vault)
            if not target.is_file() or path.exists():
                raise obsidian_cli.CliError(
                    f"obsidian move did not leave {rel} at {new_rel}")
            if target.read_bytes() != data:
                raise SplitError(f"{new_rel} changed in the move (the app "
                                 "updated its links?); split it again")
        except (OSError, obsidian_cli.CliError, SplitError) as err:
            print(f"split: {err}", file=sys.stderr)
            print(f"split: stopped; {rel} is not split", file=sys.stderr)
            return 1
        print(f"moved {rel} to {new_rel}")
        path = target
    hub_written = False
    try:
        for base, part in parts:
            part_rel = f"{prefix}{base}.md"
            obsidian_cli.create_note(vault, part_rel, part)
            created.append(part_rel)
            print(f"created {part_rel}")
        if path.read_bytes() != data:
            raise SplitError(f"{new_rel} changed while the parts were "
                             "written; split it again")
        write_atomic(path, hub.encode("utf-8"))
        hub_written = True
        print(f"wrote the hub {new_rel}")
        rewrites, left = link_scan(vault, name, sections)
        by_file = {}
        for rewrite in rewrites:
            by_file.setdefault(rewrite[0], []).append(rewrite)
        for file, group in by_file.items():
            apply_rewrites(group)
            for _, old, new in group:
                print(f"rewrote {file.relative_to(vault).as_posix()}: "
                      f"{old} -> {new}")
        for file, link, reason in left:
            print(f"left {file.relative_to(vault).as_posix()}: {link} "
                  f"({reason})")
    except (OSError, obsidian_cli.CliError, SplitError) as err:
        state = ("the hub is written" if hub_written
                 else f"{new_rel} is not overwritten")
        print(f"split: {err}", file=sys.stderr)
        print(f"split: stopped; {state}; created before the failure: "
              f"{', '.join(created) or 'nothing'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
