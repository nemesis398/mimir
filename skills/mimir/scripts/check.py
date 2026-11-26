#!/usr/bin/env python3
"""Check a vault against the rules in its ``.mimir.json``.

``check_vault`` only reads. ``fix_progress`` and ``fix_style`` are the
writers: the first sets the counted ``progress`` line of the notes whose kind
counts checkboxes, the second rewrites a frontmatter into the config's
``key_order`` and ``list_style`` and leaves one it cannot read safely as it
is. The CLI prints one ``LEVEL rel: message`` line per finding and exits 1
on any ERROR (0 with ``--report-only``), 2 on a config error.
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
_EMPTY_FLOW = re.compile(r"^\[[ \t]*\](?:[ \t]+#.*)?$")
_WIKILINK = re.compile(r"^\[\[[^\[\]]*\]\](?:[ \t]+#.*)?$")


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


def fix_style(vault: Path, cfg: config.Config) \
        -> tuple[list[Path], list[tuple[Path, str]]]:
    """Rewrite each frontmatter into the style; (changed, [(path, why not)]).

    A note in ``style_skip_names`` or without a closed frontmatter is left
    out; one that ``restyle`` cannot rewrite is listed with its reason and
    left untouched.
    """
    if not cfg.key_order and cfg.list_style is None:
        return [], []
    changed, skipped = [], []
    for note in notes.load_notes(Path(vault)):
        if note.front is None or _style_skipped(cfg, note.rel):
            continue
        data = note.path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            new, reason = restyle(data.decode("utf-8", errors="replace"), cfg)
            if new is not None or reason:
                skipped.append((note.path, "not UTF-8"))
            continue
        new, reason = restyle(text, cfg)
        if reason:
            skipped.append((note.path, reason))
        elif new is not None and new != text:
            _write(note.path, new.encode("utf-8"))
            changed.append(note.path)
    return changed, skipped


def restyle(text: str, cfg: config.Config) -> tuple[str | None, str | None]:
    """(text with its frontmatter in the style, why it cannot be rewritten).

    Flow lists become block lists when ``list_style`` is block, and the keys
    take the ``key_order``; every other line, the line endings and the body
    stay as they are. Gives (None, None) when the frontmatter is already in
    the style, (None, reason) when it is not and cannot be read safely.
    """
    layout = notes.front_entries(text)
    if layout is None:
        return None, None
    lines, end, entries = layout
    keyed = [entry for entry in entries if entry.key is not None]
    blocks, reason, needed = [], None, False
    for entry in keyed:
        block = lines[entry.start:entry.stop]
        form = _list_form(lines, entry)
        if cfg.list_style == "block" and form == "flow":
            needed = True
            try:
                block = _block_list(lines, entry)
            except ValueError as err:
                reason = reason or str(err)
        elif cfg.list_style == "flow" and form == "block":
            needed = True
            reason = reason or "a block list, --fix writes no flow lists"
        blocks.append(block)
    keys = [entry.key for entry in keyed]
    order = _key_ranks(keys, cfg.key_order)
    reordered = order != list(range(len(keyed)))
    if not (needed or reordered):
        return None, None
    if reason:
        return None, reason
    if len(keyed) != len(entries):
        return None, "a top-level line that is no key"
    if len(set(keys)) != len(keys):
        return None, "a key that appears twice"
    if notes.front_errors(text):
        return None, "a frontmatter a YAML parser would reject"
    if _has_anchor(lines, keyed):
        return None, "an anchor, alias or tag"
    if reordered:
        covered = {i for entry in keyed
                   for i in range(entry.start, entry.stop)}
        if any(i not in covered
               for i in range(keyed[0].start, keyed[-1].stop)):
            return None, "a comment or blank line between keys"
        moved = {old for slot, old in enumerate(order) if old != slot}
        if any(_keeps_tail(keyed[old].raw) for old in moved):
            return None, "a keep-chomped block scalar that would move"
        if len(keyed) - 1 in moved and any(
                not lines[i].strip() for i in range(keyed[-1].stop, end)):
            return None, "a blank line after the last key, which would move"
    starts = {entry.start: entry for entry in keyed}
    out, slot, i = [lines[0]], 0, 1
    while i < end:
        if i in starts:
            out += blocks[order[slot]]
            i, slot = starts[i].stop, slot + 1
        else:
            out.append(lines[i])
            i += 1
    new = "".join(out + lines[end:])
    if not _same_front(text, new) or notes.front_errors(new):
        return None, "the rewrite would change a value"
    return new, None


def _same_front(old: str, new: str) -> bool:
    """Whether new's frontmatter and body read as old's do.

    The lenient parser drops the blank lines after a block scalar, which a
    keep-chomped one (``|+``, ``>+``) keeps, so they are counted here too.
    """
    return notes.parse_front(new) == notes.parse_front(old) \
        and _block_tails(new) == _block_tails(old)


def _block_tails(text: str) -> dict[str, int]:
    """The number of blank lines after each block scalar key of text."""
    layout = notes.front_entries(text)
    if layout is None:
        return {}
    lines, end, entries = layout
    tails = {}
    for at, entry in enumerate(entries):
        if entry.key is None or not entry.raw.startswith(("|", ">")):
            continue
        stop = entries[at + 1].start if at + 1 < len(entries) else end
        count = 0
        for line in lines[entry.stop:stop]:
            if line.strip():
                break
            count += 1
        tails[entry.key] = count
    return tails


def _keeps_tail(raw: str) -> bool:
    """Whether raw is a block scalar header with the keep (+) indicator."""
    return raw.startswith(("|", ">")) and "+" in raw.split("#", 1)[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check.py", description="Check a vault against its .mimir.json.")
    parser.add_argument("--vault", type=Path, default=None,
                        help="the vault root (default: $MIMIR_VAULT, then .)")
    parser.add_argument("--fix", action="store_true",
                        help="write the counted progress and the "
                             "frontmatter style first")
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
        changed, skipped = fix_style(vault, cfg)
        for path in changed:
            rel = path.relative_to(vault).as_posix()
            print(f"fixed {rel}: style", file=sys.stderr)
        for path, reason in skipped:
            rel = path.relative_to(vault).as_posix()
            print(f"WARN {rel}: style: not rewritten, {reason}",
                  file=sys.stderr)
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
        """(note, expected) for each note whose kind counts checkboxes.

        A done note expects 100 whatever its boxes say; a cancelled note's
        progress is not checked.
        """
        for note in self.notes:
            if note.front is None or note.rel in self.hub_of \
                    or self.is_template(note):
                continue
            if not any(k.progress == "checkboxes"
                       for k in self.kinds_of(note)):
                continue
            status = str(note.front.get("status"))
            if status == "cancelled":
                continue
            texts = [note.body] + [p.body for p in self.parts.get(note.rel, [])]
            counted = count_progress(texts)
            if status == "done" and (counted is not None
                                     or "progress" in note.front):
                counted = 100
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
        return found + _check_front_required(ctx, note)
    text = note.path.read_bytes().decode("utf-8", errors="replace")
    for problem in notes.front_errors(text):
        error(problem)
    found += _check_style(ctx, note, text)
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


def _check_front_required(ctx: _Context, note: notes.Note) -> list[Finding]:
    """An ERROR for a note without frontmatter in a front_required folder."""
    if ctx.is_template(note):
        return []
    dirs = note.rel.split("/")[:-1]
    for kind in ctx.cfg.kinds.values():
        if kind.front_required and kind.folder and kind.folder in dirs:
            return [Finding("ERROR", note.rel,
                            f"no frontmatter, kind {kind.name} requires it")]
    return []


def _check_style(ctx: _Context, note: notes.Note, text: str) -> list[Finding]:
    """The ``list style`` and ``key order`` findings of one note."""
    cfg = ctx.cfg
    if (not cfg.key_order and cfg.list_style is None) \
            or _style_skipped(cfg, note.rel):
        return []
    layout = notes.front_entries(text)
    if layout is None:
        return []
    lines, _, entries = layout
    keyed = [entry for entry in entries if entry.key is not None]
    found = []
    if cfg.list_style:
        other = "flow" if cfg.list_style == "block" else "block"
        wrong = [entry.key for entry in keyed
                 if _list_form(lines, entry) == other]
        if wrong:
            what = f"is a {other} list" if len(wrong) == 1 \
                else f"are {other} lists"
            found.append(Finding(
                "ERROR", note.rel,
                f"list style: {', '.join(wrong)} {what}, list_style is "
                f"{cfg.list_style}"))
    if cfg.key_order:
        keys = [entry.key for entry in keyed]
        expected = [keys[i] for i in _key_ranks(keys, cfg.key_order)]
        for have, want in zip(keys, expected):
            if have != want:
                found.append(Finding(
                    "ERROR", note.rel,
                    f"key order: {want} belongs before {have}"))
                break
    return found


def _key_ranks(keys: list[str], order: tuple[str, ...]) -> list[int]:
    """The indices of keys in the style's order: order's keys, then the rest."""
    rank = {key: i for i, key in enumerate(order)}
    return sorted(range(len(keys)),
                  key=lambda i: (rank.get(keys[i], len(order)), i))


def _list_form(lines: list[str], entry: notes.Entry) -> str | None:
    """"flow" or "block" when entry's value is a non-empty list, else None."""
    raw = entry.raw
    if raw.startswith("[") and not _WIKILINK.match(raw) \
            and not _EMPTY_FLOW.match(raw):
        return "flow"
    if raw and not raw.startswith("#"):
        return None
    content = [line.strip() for line in lines[entry.start + 1:entry.stop]]
    content = [c for c in content if c and not c.startswith("#")]
    if content and all(c == "-" or c.startswith("- ") for c in content):
        return "block"
    return None


def _block_list(lines: list[str], entry: notes.Entry) -> list[str]:
    """The lines of entry's one-line flow list as a block list."""
    if re.match(r"\[[ \t]*\[\[", entry.raw):
        raise ValueError("a flow list of unquoted wikilinks, YAML reads "
                         "each as a nested list; quote them")
    items = notes.flow_items(entry.raw)
    if entry.stop > entry.start + 1:
        raise ValueError("a multi-line flow list")
    if not items:
        raise ValueError("an empty flow list")
    line = lines[entry.start]
    ending = line[len(line.rstrip("\r\n")):] or "\n"
    head = line[:line.index(":") + 1]
    return [head + ending] + [f"  - {_block_item(item)}{ending}"
                              for item in items]


def _block_item(item: str) -> str:
    """A flow item as a block item: a single-quoted wikilink gets "..."."""
    if item.startswith("'"):
        value = notes.scalar(item)
        if isinstance(value, str) and value.startswith("[[") \
                and '"' not in value and "\\" not in value:
            return f'"{value}"'
    return item


def _has_anchor(lines: list[str], entries: list[notes.Entry]) -> bool:
    """Whether a value or a block list item starts with &, * or !."""
    for entry in entries:
        if entry.raw[:1] in ("&", "*", "!"):
            return True
        for line in lines[entry.start + 1:entry.stop]:
            item = line.strip()
            if item.startswith("- ") and item[2:].lstrip()[:1] in "&*!" \
                    and item[2:].strip():
                return True
    return False


def _style_skipped(cfg: config.Config, rel: str) -> bool:
    filename = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(filename, pattern)
               or fnmatch.fnmatchcase(_basename(rel), pattern)
               for pattern in cfg.style_skip_names)


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
    """An ERROR for each property that two bases label differently.

    `file.name` is exempt: the name column says what each base lists.
    """
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
        if len(by_label) < 2 or prop == "file.name":
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
        want = "a done note has 100" if str(note.front.get("status")) \
            == "done" else f"counted {counted}"
        if have is None:
            found.append(Finding(
                "ERROR", note.rel, f"progress missing, {want}"))
        elif have != counted or isinstance(have, bool):
            found.append(Finding(
                "ERROR", note.rel, f"progress {have}, {want}"))
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
    _write(path, "".join(lines[:1] + head + lines[end:]).encode("utf-8"))
    return True


def _write(path: Path, data: bytes) -> None:
    """Replace path's content with data in one step, keeping its mode."""
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
