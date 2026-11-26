#!/usr/bin/env python3
"""Check a vault against the rules in its ``.mimir.json``.

``check_vault`` only reads. ``fix_progress`` is the one writer: it sets the
counted ``progress`` line of the notes whose kind counts checkboxes. The CLI
prints one ``LEVEL rel: message`` line per finding and exits 1 on any ERROR
(0 with ``--report-only``), 2 on a config error.
"""

import argparse
import datetime
import fnmatch
import os
import re
import stat
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import notes  # noqa: E402

PROJECT_TYPE = "project"
CLOSED_STATES = ("done", "cancelled")
PARTS_HEADING = "Parts"
PART_SEPARATOR = " – "

_BOX = re.compile(r"^[ \t]*(?:[-*+]|\d+[.)])[ \t]+\[(.)\]")
_FENCE = re.compile(r"^[ \t]*(`{3,}|~{3,})(.*)$")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t#]*$")
_NOTE_REF = re.compile(
    r"\bnote\.([A-Za-z_]\w*(?:-[A-Za-z_]\w*)*)"
    r"|\bnote\[\s*[\"']([^\"']+)[\"']\s*\]")
_ORDER = re.compile(r"^([ \t]*)(?:-[ \t]+)?order:[ \t]*(.*)$")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PROPERTIES = re.compile(r"^properties[ \t]*:[ \t]*(?:#.*)?$")
_BASE_KEY = re.compile(
    r"^[ \t]+(\"[^\"]*\"|'[^']*'|[^\s:#\"'][^:]*?):(?:[ \t]+(.*?))?[ \t]*$")
_DISPLAY = re.compile(r"^displayName[ \t]*:(?:[ \t]+(.*?))?[ \t]*$")
_DISPLAY_FLOW = re.compile(
    r"\bdisplayName[ \t]*:[ \t]*(\"[^\"]*\"|'[^']*'|[^,}]+)")


@dataclass(frozen=True)
class Finding:
    level: Literal["ERROR", "WARN", "INFO"]
    rel: str
    message: str


def count_progress(texts: list[str]) -> int | None:
    """Percent of done (``[x]``, ``[-]``) checkboxes outside fenced code."""
    done = total = 0
    for text in texts:
        for line in _unfenced(text):
            match = _BOX.match(line)
            if match:
                total += 1
                if match.group(1) in ("x", "X", "-"):
                    done += 1
    if not total:
        return None
    return (200 * done + total) // (2 * total)


def check_vault(vault: Path, cfg: config.Config) -> list[Finding]:
    """Every finding in vault under cfg, sorted by path."""
    vault = Path(vault)
    ctx = _Context(vault, cfg)
    found: list[Finding] = []
    found += _check_root(vault, cfg)
    found += _check_hub_folders(vault, cfg)
    found += _check_bases(vault, ctx.known)
    found += _check_templates(vault, cfg)
    found += _check_labels(ctx)
    for note in ctx.notes:
        found += _check_note(ctx, note)
    found += _check_progress(ctx)
    found.sort(key=lambda finding: finding.rel)
    return found


def fix_progress(vault: Path, cfg: config.Config) -> list[Path]:
    """Write the counted progress where it differs; the changed paths."""
    ctx = _Context(Path(vault), cfg)
    changed = []
    for note, counted in ctx.progress_targets():
        if note.front.get("progress") == counted and \
                not isinstance(note.front.get("progress"), bool):
            continue
        if _set_progress(note.path, counted):
            changed.append(note.path)
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py", description="Check a vault against its .mimir.json.")
    parser.add_argument("--vault", type=Path, default=None,
                        help="the vault root (default: $MIMIR_VAULT, then .)")
    parser.add_argument("--fix", action="store_true",
                        help="write the counted progress first")
    parser.add_argument("--report-only", action="store_true",
                        help="exit 0 even when there are errors")
    args = parser.parse_args(argv)
    vault = args.vault or Path(os.environ.get("MIMIR_VAULT") or ".")
    try:
        cfg = config.load(vault)
    except config.ConfigError as err:
        print(f"mimir: {err}", file=sys.stderr)
        return 2
    if args.fix:
        for path in fix_progress(vault, cfg):
            rel = path.relative_to(vault).as_posix()
            print(f"fixed {rel}: progress", file=sys.stderr)
    findings = check_vault(vault, cfg)
    for finding in findings:
        print(f"{finding.level} {finding.rel}: {finding.message}")
    if not args.report_only and any(f.level == "ERROR" for f in findings):
        return 1
    return 0


class _Context:
    """The loaded notes and what the rules look up in them."""

    def __init__(self, vault: Path, cfg: config.Config):
        self.vault, self.cfg = vault, cfg
        self.notes = notes.load_notes(vault)
        self.by_name: dict[str, list[notes.Note]] = {}
        for note in self.notes:
            self.by_name.setdefault(_basename(note.rel), []).append(note)
        self.known = cfg.view_properties | cfg.allowed_keys | {
            key for kind in cfg.kinds.values() for key in kind.required}
        self.template_dirs = set()
        self.template_files = set()
        for kind in cfg.kinds.values():
            if kind.template:
                rel = Path(kind.template).as_posix()
                self.template_files.add(rel)
                parent = Path(rel).parent.as_posix()
                if parent != ".":
                    self.template_dirs.add(parent)
        self.parts: dict[str, list[notes.Note]] = {}
        self.hub_of: dict[str, notes.Note] = {}
        for note in self.notes:
            parts = self._find_parts(note)
            if parts:
                self.parts[note.rel] = parts
                for part in parts:
                    self.hub_of.setdefault(part.rel, note)
        self.tag_re = re.compile(cfg.tag_pattern) if cfg.tag_pattern \
            else None
        words = sorted(set(cfg.warn_words), key=len, reverse=True)
        self.warn_re = re.compile(
            r"(?<!\w)(?:" + "|".join(map(re.escape, words)) + r")(?!\w)",
            re.IGNORECASE) if words else None
        self.name_res = {kind.name: re.compile(kind.name_pattern)
                         for kind in cfg.kinds.values() if kind.name_pattern}

    def _find_parts(self, hub: notes.Note) -> list[notes.Note]:
        prefix = _basename(hub.rel) + PART_SEPARATOR
        parent = Path(hub.rel).parent
        found = []
        for name in _parts_links(hub.body):
            if not name.startswith(prefix):
                continue
            candidates = sorted(self.by_name.get(name, []),
                                key=lambda n: Path(n.rel).parent != parent)
            if candidates and candidates[0] not in found:
                found.append(candidates[0])
        return found

    def is_template(self, note: notes.Note) -> bool:
        return note.rel in self.template_files or any(
            note.rel.startswith(d + "/") for d in self.template_dirs)

    def kinds_of(self, note: notes.Note) -> list[config.Kind]:
        note_type = _type(note)
        if note_type is None:
            return []
        kinds = [k for k in self.cfg.kinds.values() if k.note_type == note_type]
        if len(kinds) > 1:
            dirs = note.rel.split("/")[:-1]
            kinds = ([k for k in kinds if k.folder and k.folder in dirs]
                     or [k for k in kinds if not k.folder] or kinds)
        return kinds

    def progress_targets(self):
        """(note, counted) for each note whose kind counts checkboxes."""
        for note in self.notes:
            if note.front is None or note.rel in self.hub_of \
                    or self.is_template(note):
                continue
            if not any(k.progress == "checkboxes"
                       for k in self.kinds_of(note)):
                continue
            texts = [note.body] + [p.body for p in self.parts.get(note.rel, [])]
            counted = count_progress(texts)
            if counted is not None:
                yield note, counted


def _check_note(ctx: _Context, note: notes.Note) -> list[Finding]:
    cfg, front, rel = ctx.cfg, note.front, note.rel
    found = []

    def error(message):
        found.append(Finding("ERROR", rel, message))

    if note.size > cfg.size_cap_bytes and not _size_skipped(cfg, note):
        found.append(Finding(
            "WARN", rel, f"{note.size} bytes, over the cap of "
                         f"{cfg.size_cap_bytes}"))
    if front is None:
        return found
    text = note.path.read_bytes().decode("utf-8", errors="replace")
    for problem in notes.front_errors(text):
        error(problem)
    note_type = _type(note)
    status = front.get("status")
    if status is not None:
        allowed = cfg.states + cfg.states_by_type.get(note_type or "", ())
        if str(status) not in allowed:
            error(f'status "{status}" is not allowed'
                  + (f" on type {note_type}" if note_type else ""))
    if note_type is not None:
        for key in front:
            if key not in ctx.known:
                error(f"unknown key {key}")
    is_part = rel in ctx.hub_of
    if is_part and "progress" in front:
        error("a part carries no progress, its hub counts it")
    found += _check_tags(ctx, note)
    found += _check_warn_words(ctx, note)
    if ctx.is_template(note):
        return found
    found += _check_dates(ctx, note, text)
    if not is_part:
        for kind in ctx.kinds_of(note):
            for key in kind.required:
                if _empty(front.get(key)):
                    error(f"{key} missing, kind {kind.name} requires it")
            pattern = ctx.name_res.get(kind.name)
            if pattern and not pattern.fullmatch(_basename(rel)):
                error(f"name does not match {kind.name_pattern}, the "
                      f"name_pattern of kind {kind.name}")
    if str(status) == "done":
        for part in ctx.parts.get(rel, []):
            part_status = (part.front or {}).get("status")
            if part_status is not None and \
                    str(part_status) not in CLOSED_STATES:
                error(f"done, but part {_basename(part.rel)} is "
                      f"{part_status}")
    if not _empty(front.get("project")):
        found += _check_project(ctx, rel, front["project"])
    return found


def _check_dates(ctx: _Context, note: notes.Note, text: str) -> list[Finding]:
    """An ERROR for each date key whose value is no unquoted YYYY-MM-DD."""
    found, raw = [], None
    for key in ctx.cfg.date_keys:
        if _empty(note.front.get(key)):
            continue
        if raw is None:
            raw = notes.front_raw(text)
        value = raw.get(key, "")
        if not _is_date(value.split(" #", 1)[0].strip()):
            shown = value or "a value on the lines below it"
            found.append(Finding(
                "ERROR", note.rel,
                f"{key} is {shown}, not an unquoted YYYY-MM-DD date"))
    return found


def _is_date(text: str) -> bool:
    if not _DATE.fullmatch(text):
        return False
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def _check_tags(ctx: _Context, note: notes.Note) -> list[Finding]:
    value = note.front.get("tags")
    if ctx.tag_re is None or _empty(value):
        return []
    items = value if isinstance(value, list) else [value]
    bad = [str(item) for item in items
           if not ctx.tag_re.fullmatch(str(item))]
    if not bad:
        return []
    return [Finding("ERROR", note.rel,
                    f"tags {', '.join(bad)} do not match the tag_pattern "
                    f"{ctx.cfg.tag_pattern}")]


def _check_warn_words(ctx: _Context, note: notes.Note) -> list[Finding]:
    value = note.front.get("description")
    if ctx.warn_re is None or _empty(value):
        return []
    text = " ".join(map(str, value)) if isinstance(value, list) \
        else str(value)
    hits = []
    for match in ctx.warn_re.finditer(text):
        word = match.group(0).lower()
        if word not in hits:
            hits.append(word)
    if not hits:
        return []
    return [Finding("WARN", note.rel,
                    f"description contains {', '.join(hits)}, from "
                    f"warn_words")]


def _check_labels(ctx: _Context) -> list[Finding]:
    """An ERROR for each property that two bases label differently."""
    sources = []
    for path in _walk(ctx.vault, ".base"):
        sources.append((path.relative_to(ctx.vault).as_posix(),
                        path.read_bytes().decode("utf-8", errors="replace")))
    for note in ctx.notes:
        sources += [(note.rel, block) for block in _base_blocks(note.body)]
    labels: dict[str, dict[str, list[str]]] = {}
    for rel, text in sources:
        for prop, label in _base_labels(text):
            where = labels.setdefault(prop, {}).setdefault(label, [])
            if rel not in where:
                where.append(rel)
    found = []
    for prop, by_label in sorted(labels.items()):
        if len(by_label) < 2:
            continue
        groups = sorted((sorted(rels), label)
                        for label, rels in by_label.items())
        shown = prop[len("note."):] if prop.startswith("note.") else prop
        text = "; ".join(f'"{label}" in {", ".join(rels)}'
                         for rels, label in groups)
        found.append(Finding(
            "ERROR", groups[0][0][0],
            f"property {shown} has {len(groups)} labels: {text}"))
    return found


def _base_labels(text: str) -> list[tuple[str, str]]:
    """The (property, displayName) pairs under a base's ``properties:``.

    A bare property name means ``note.<name>``; formulas are left out,
    since each base defines its own.
    """
    pairs, inside, child, current = [], False, None, None

    def add(prop, raw):
        label = notes.scalar(raw) if raw else None
        if prop and label is not None and str(label):
            pairs.append((prop, str(label)))

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            inside, child, current = bool(_PROPERTIES.match(line)), None, None
            continue
        if not inside:
            continue
        if child is None:
            child = indent
        if indent <= child:
            match = _BASE_KEY.match(line)
            current = _label_property(str(notes.scalar(match.group(1)))) \
                if match else None
            rest = (match.group(2) or "") if match else ""
            flow = _DISPLAY_FLOW.search(rest) if rest.startswith("{") \
                else None
            if flow:
                add(current, flow.group(1).strip())
            continue
        match = _DISPLAY.match(stripped)
        if match:
            add(current, match.group(1))
    return pairs


def _label_property(name: str) -> str | None:
    if name.startswith("formula."):
        return None
    return name if "." in name else "note." + name


def _base_blocks(body: str) -> list[str]:
    """The contents of the fenced code blocks of language ``base``."""
    blocks, fence, is_base, rows = [], None, False, []
    for line in body.splitlines():
        match = _FENCE.match(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`"
                              and "`" in match.group(2)):
                fence, rows = match.group(1), []
                is_base = match.group(2).strip().split()[:1] == ["base"]
        elif match and match.group(1)[0] == fence[0] \
                and len(match.group(1)) >= len(fence) \
                and not match.group(2).strip():
            if is_base:
                blocks.append(textwrap.dedent("\n".join(rows)))
            fence = None
        else:
            rows.append(line)
    return blocks


def _check_project(ctx: _Context, rel: str, value) -> list[Finding]:
    targets = notes.links(value)
    if not targets:
        return [Finding("ERROR", rel, "project is not a wikilink")]
    found = []
    for target in targets:
        candidates = ctx.by_name.get(target, [])
        if not candidates:
            found.append(Finding(
                "ERROR", rel, f"project [[{target}]] does not resolve"))
        elif not any(_type(n) == PROJECT_TYPE for n in candidates):
            found.append(Finding(
                "ERROR", rel,
                f"project [[{target}]] is not a type {PROJECT_TYPE} note"))
    return found


def _check_progress(ctx: _Context) -> list[Finding]:
    found = []
    for note, counted in ctx.progress_targets():
        have = note.front.get("progress")
        if have is None:
            found.append(Finding(
                "ERROR", note.rel, f"progress missing, counted {counted}"))
        elif have != counted or isinstance(have, bool):
            found.append(Finding(
                "ERROR", note.rel, f"progress {have}, counted {counted}"))
    return found


def _check_root(vault: Path, cfg: config.Config) -> list[Finding]:
    found = []
    folders = set(cfg.areas) | set(cfg.system_folders)
    for entry in sorted(vault.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_dir():
            if entry.name not in folders:
                found.append(Finding(
                    "ERROR", entry.name,
                    "folder at the root is no area and no system folder"))
        elif not any(fnmatch.fnmatchcase(entry.name, pattern)
                     for pattern in cfg.root_allowlist):
            found.append(Finding("ERROR", entry.name,
                                 "not allowed at the root"))
    return found


def _check_hub_folders(vault: Path, cfg: config.Config) -> list[Finding]:
    kind_folders = {k.folder for k in cfg.kinds.values() if k.folder}
    bases = {}
    for kind in cfg.kinds.values():
        if kind.hub:
            for area in cfg.areas:
                base = vault / area / kind.folder if kind.folder \
                    else vault / area
                bases[base] = kind.folder is None
    found = []
    for base, at_area in sorted(bases.items()):
        if not base.is_dir():
            continue
        for sub in sorted(base.iterdir()):
            if not sub.is_dir() or sub.name.startswith("."):
                continue
            if at_area and sub.name in kind_folders:
                continue
            if (sub / f"{sub.name}.md").is_file() or not _has_notes(sub):
                continue
            found.append(Finding(
                "ERROR", sub.relative_to(vault).as_posix(),
                f"folder without its hub {sub.name}.md"))
    return found


def _check_bases(vault: Path, known: frozenset) -> list[Finding]:
    found = []
    for path in _walk(vault, ".base"):
        rel = path.relative_to(vault).as_posix()
        text = path.read_bytes().decode("utf-8", errors="replace")
        unknown = []
        for name in _base_properties(text):
            if name not in known and name not in unknown:
                unknown.append(name)
        found += [Finding("ERROR", rel, f"unknown property {name}")
                  for name in unknown]
    return found


def _check_templates(vault: Path, cfg: config.Config) -> list[Finding]:
    found = []
    for kind in cfg.kinds.values():
        if not kind.template:
            continue
        rel = Path(kind.template).as_posix()
        path = vault / rel
        if not path.is_file():
            found.append(Finding(
                "WARN", rel, f"template of kind {kind.name} is missing"))
            continue
        if kind.has_status:
            text = path.read_bytes().decode("utf-8", errors="replace")
            front, _ = notes.parse_front(text)
            if front is None or "status" not in front:
                found.append(Finding(
                    "WARN", rel,
                    f"template of kind {kind.name} has no status"))
    return found


def _base_properties(text: str) -> list[str]:
    """The ``note.<prop>`` names and the bare names in ``order:`` lists."""
    names = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        for match in _NOTE_REF.finditer(line):
            names.append(match.group(1) or match.group(2))
        order = _ORDER.match(line)
        if not order:
            continue
        rest = order.group(2).strip()
        items = []
        if rest.startswith("["):
            items = rest[1:rest.rfind("]") if "]" in rest else None].split(",")
        elif not rest or rest.startswith("#"):
            indent = len(line) - len(line.lstrip())
            for nxt in lines[i + 1:]:
                stripped = nxt.strip()
                if not stripped or stripped.startswith("#"):
                    continue
                if len(nxt) - len(nxt.lstrip()) < indent or \
                        not (stripped == "-" or stripped.startswith("- ")):
                    break
                items.append(stripped[1:])
        for item in items:
            item = item.split(" #", 1)[0].strip().strip("\"'").strip()
            if item and "." not in item:
                names.append(item)
    return names


def _parts_links(body: str) -> list[str]:
    """The link targets under the body's ``Parts`` heading."""
    found, level = [], 0
    for line in _unfenced(body):
        match = _HEADING.match(line)
        if match:
            depth = len(match.group(1))
            if level and depth <= level:
                level = 0
            if match.group(2).strip() == PARTS_HEADING:
                level = depth
            continue
        if level:
            found += notes.links(line)
    return found


def _unfenced(text: str):
    """The lines of text outside fenced code blocks."""
    fence = None
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`"
                              and "`" in match.group(2)):
                fence = match.group(1)
                continue
            yield line
        elif match and match.group(1)[0] == fence[0] \
                and len(match.group(1)) >= len(fence) \
                and not match.group(2).strip():
            fence = None


def _set_progress(path: Path, value: int) -> bool:
    """Set the frontmatter's progress line to value; False if it cannot."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return False
    lines = text.splitlines(keepends=True)
    end = next((i for i in range(1, len(lines))
                if lines[i].rstrip("\r\n").rstrip() == "---"), None)
    if not lines or lines[0].rstrip("\r\n") != "---" or end is None:
        return False
    head = lines[1:end]
    at = next((i for i, line in enumerate(head)
               if line.startswith("progress:")), None)
    if at is not None:
        head[at] = f"progress: {value}" + _ending(head[at], lines[0])
    else:
        after = next((i for i, line in enumerate(head)
                      if line.startswith("status:")), len(head) - 1)
        ending = _ending(head[after] if head else "", lines[0])
        head.insert(after + 1, f"progress: {value}{ending}")
    data = "".join(lines[:1] + head + lines[end:]).encode("utf-8")
    handle, tmp = tempfile.mkstemp(dir=path.parent, prefix=".mimir-",
                                   suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(data)
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return True


def _ending(line: str, fallback: str) -> str:
    """The line ending of line, else of fallback, else a newline."""
    for text in (line, fallback):
        ending = text[len(text.rstrip("\r\n")):]
        if ending:
            return ending
    return "\n"


def _walk(vault: Path, suffix: str):
    for root, dirs, files in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(files):
            if name.endswith(suffix):
                yield Path(root) / name


def _has_notes(folder: Path) -> bool:
    return any(True for _ in _walk(folder, ".md"))


def _size_skipped(cfg: config.Config, note: notes.Note) -> bool:
    for folder in cfg.size_skip_folders:
        folder = folder.strip("/")
        if note.rel == folder or note.rel.startswith(folder + "/"):
            return True
    if _type(note) in cfg.size_skip_types:
        return True
    filename = note.rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(filename, pattern)
               or fnmatch.fnmatchcase(_basename(note.rel), pattern)
               for pattern in cfg.size_skip_names)


def _type(note: notes.Note) -> str | None:
    value = (note.front or {}).get("type")
    return value if isinstance(value, str) and value else None


def _basename(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def _empty(value) -> bool:
    return value is None or value == "" or value == []


if __name__ == "__main__":
    sys.exit(main())
