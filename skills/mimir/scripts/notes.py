"""Read the notes of a vault and their frontmatter.

The frontmatter reader is a small YAML subset, enough for Obsidian
properties: plain, quoted and block scalars, flow and block lists. A nested
map (an indented block under an empty key) is passed through as its raw
text. An unquoted value that starts with ``[[`` stays a string, so that
``related: [[A]]`` yields its link instead of a nested list.

The reader is lenient; ``front_errors`` names what a YAML parser would
reject, such as ``: `` inside an unquoted value, which Obsidian then marks
as invalid properties.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

_KEY = re.compile(r"^([^\s#\-][^:]*?):(?:[ \t]+(.*?))?[ \t]*$")
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$")
_BLOCK = {"|", "|-", "|+", ">", ">-", ">+"}
_COLON = re.compile(r":(?:[ \t]|$)")
_COMMENT = re.compile(r"[ \t]#")
_HEADER = re.compile(r"^[|>](?:[1-9][-+]?|[-+][1-9]?)?(?:[ \t]+#.*)?$")
_RESERVED = "@`%]},*"
_LINK = re.compile(r"\[\[([^\[\]|#]*)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")


@dataclass(frozen=True)
class Note:
    path: Path
    rel: str
    front: dict | None
    body: str
    size: int


@dataclass(frozen=True)
class Entry:
    """One top-level frontmatter line and the lines of its value.

    ``start`` and ``stop`` index the note's lines (``stop`` just past the
    last line of the value); ``key`` is None for a top-level line that is
    no ``key:`` line, and ``raw`` is the stripped text after the colon.
    """
    key: str | None
    raw: str
    start: int
    stop: int


def parse_front(text: str) -> tuple[dict | None, str]:
    """Split text into (frontmatter dict, body); (None, text) without one."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text
    end = front_end(lines)
    if end is None:
        return None, text
    inner = [line.rstrip("\r\n") for line in lines[1:end]]
    return _parse_map(inner), "".join(lines[end + 1:])


def front_end(lines: list[str]) -> int | None:
    """The index of the closing ``---`` line after the opening one, or None."""
    for end in range(1, len(lines)):
        if lines[end].rstrip() == "---":
            return end
    return None


def front_errors(text: str) -> list[str]:
    """What a YAML parser would reject in text's frontmatter, in line order.

    Each message reads ``frontmatter line <n>: <problem>``, n counting the
    note's lines from 1, the opening ``---``. One message per value at most;
    text without a closed frontmatter has none.
    """
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        return []
    end = front_end(lines)
    if end is None:
        return []
    problems = []
    for index, line, match, children in _top_lines(lines[1:end]):
        problems += _line_problems(index, line, match, children)
    problems.sort(key=lambda problem: problem[0])
    return [f"frontmatter line {index + 2}: {problem}"
            for index, problem in problems]


def front_raw(text: str) -> dict[str, str]:
    """The unparsed text after ``key:`` of each top-level frontmatter key.

    The value's own line only, stripped; a value on the lines below its key
    gives "". Without a closed frontmatter the dict is empty.
    """
    lines = text.splitlines()
    if not lines or lines[0].rstrip() != "---":
        return {}
    end = front_end(lines)
    if end is None:
        return {}
    return {_unquote(match.group(1).strip()): (match.group(2) or "").strip()
            for _, _, match, _ in _top_lines(lines[1:end]) if match}


def front_entries(text: str) -> tuple[list[str], int, list[Entry]] | None:
    """(lines with their endings, index of the closing ``---``, entries).

    The lines split as ``parse_front`` splits them, so joining
    ``lines[end:]`` gives the closing line and the body byte for byte.
    Top-level blank and comment lines belong to no entry. None without a
    closed frontmatter.
    """
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None
    end = front_end(lines)
    if end is None:
        return None
    inner = [line.rstrip("\r\n") for line in lines[1:end]]
    entries = []
    for index, _, match, children in _top_lines(inner):
        stop = (children[-1][0] if children else index) + 2
        key = _unquote(match.group(1).strip()) if match else None
        raw = (match.group(2) or "").strip() if match else ""
        entries.append(Entry(key, raw, index + 1, stop))
    return lines, end, entries


def flow_items(raw: str) -> list[str]:
    """The source text of each item of the one-line flow list raw.

    Quoted items keep their quotes. Raises ValueError naming what cannot be
    carried into a block list safely: a nested list or map, a list that
    goes on below its line, an anchor, alias or tag, a comment or text
    after the closing bracket, an item YAML would read otherwise.
    """
    if not raw.startswith("["):
        raise ValueError("no flow list")
    items, i, size = [], 1, len(raw)

    def skip(at):
        while at < size and raw[at] in " \t":
            at += 1
        return at

    while True:
        i = skip(i)
        if i >= size:
            raise ValueError("a multi-line flow list")
        char = raw[i]
        if char == "]":
            break
        if char in "[{":
            raise ValueError("a nested flow list")
        if char in "&*!":
            raise ValueError("an anchor, alias or tag in a flow list")
        if char in "\"'":
            close = _quote_close(raw, i)
            if close is None:
                raise ValueError("a multi-line flow list")
            item, i = raw[i:close], skip(close)
            if i >= size:
                raise ValueError("a multi-line flow list")
            if raw[i] not in ",]":
                raise ValueError("text after a quoted item")
        else:
            stop = i
            while stop < size and raw[stop] not in ",[]{}":
                stop += 1
            item, i = raw[i:stop].rstrip(), stop
            if i >= size:
                raise ValueError("a multi-line flow list")
            if raw[i] in "[{":
                raise ValueError("a nested flow list")
            if raw[i] == "}":
                raise ValueError("a flow map in a flow list")
            problem = _flow_item_problem(item)
            if problem:
                raise ValueError(problem)
        items.append(item)
        if raw[i] == ",":
            i += 1
            continue
        break
    rest = raw[i + 1:].strip()
    if rest.startswith("#"):
        raise ValueError("a comment after a flow list")
    if rest:
        raise ValueError("text after a flow list")
    return items


def _flow_item_problem(item: str) -> str | None:
    """Why the plain flow list item cannot become a block item, or None."""
    if not item:
        return "an empty item in a flow list"
    if item.startswith("#") or _COMMENT.search(item):
        return "a comment inside a flow list"
    if _COLON.search(item) or item.endswith(":"):
        return "a flow map in a flow list"
    if item[0] in _RESERVED or item[0] in "|>":
        return f'an item that starts with "{item[0]}"'
    if item[0] in "-?" and (len(item) == 1 or item[1] in " \t"):
        return f'an item that starts with "{item[0]} "'
    return None


def scalar(text: str):
    """One YAML scalar or flow list as the frontmatter reader reads it."""
    return _scalar(text)


def links(value) -> list[str]:
    """The target basenames of the wikilinks in a property value or list."""
    if isinstance(value, str):
        found = []
        for target in _LINK.findall(value):
            target = target.strip()
            if not target:
                continue
            base = target.rsplit("/", 1)[-1]
            if base.endswith(".md"):
                base = base[:-3]
            found.append(base)
        return found
    if isinstance(value, (list, tuple)):
        return [name for item in value for name in links(item)]
    return []


def load_notes(vault: Path) -> list[Note]:
    """Every ``*.md`` note under vault, skipping dot-directories at any depth."""
    vault = Path(vault)
    loaded = []
    for root, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if not name.endswith(".md"):
                continue
            path = Path(root) / name
            data = path.read_bytes()
            front, body = parse_front(data.decode("utf-8", errors="replace"))
            rel = path.relative_to(vault).as_posix()
            loaded.append(Note(path, rel, front, body, len(data)))
    loaded.sort(key=lambda note: note.rel)
    return loaded


def _indented(line: str) -> bool:
    return line[:1] in (" ", "\t")


def _top_lines(lines: list[str]):
    """Each top-level line with the lines that belong to its value.

    Yields (index, line, key match or None, [(index, child), ...]); blank
    and comment lines at the top level are skipped, and only a key line
    gathers children.
    """
    i, count = 0, len(lines)
    while i < count:
        line, start = lines[i], i
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = None if _indented(line) else _KEY.match(line)
        children = []
        if match:
            raw = (match.group(2) or "").strip()
            while i < count:
                nxt = lines[i]
                if _indented(nxt) or not nxt.strip() or (
                        raw == "" and (nxt == "-" or nxt.startswith("- "))):
                    children.append((i, nxt))
                    i += 1
                else:
                    break
            while children and not children[-1][1].strip():
                children.pop()
        yield start, line, match, children


def _parse_map(lines: list[str]) -> dict:
    result = {}
    for _, _, match, children in _top_lines(lines):
        if match is None:
            continue
        key = _unquote(match.group(1).strip())
        raw = (match.group(2) or "").strip()
        result[key] = _value(raw, [child for _, child in children])
    return result


def _line_problems(index, line, match, children) -> list[tuple[int, str]]:
    """The (index, problem) pairs of one top-level line and its value."""
    problems = []
    raw = (match.group(2) or "").strip() if match else ""
    block = raw[:1] in ("|", ">")
    for at, child in children:
        lead = child[:len(child) - len(child.lstrip())]
        if child.startswith("\t") if block else "\t" in lead:
            problems.append((at, "a tab in the indentation"))
            return problems
    if match is None:
        lead = line[:len(line) - len(line.lstrip())]
        if "\t" in lead:
            problems.append((index, "a tab in the indentation"))
        elif _indented(line):
            pass
        elif line == "-" or line.startswith("- "):
            problems.append((index, "a list item after a value, put it "
                                    "under a key without a value"))
        elif not _COLON.search(line):
            problems.append((index, 'no "key: value" on this line'))
        return problems
    content = [(at, child.strip()) for at, child in children if child.strip()]
    if block:
        if not _HEADER.match(raw):
            problems.append((index, f"a bad block scalar header {raw}"))
        return problems
    if raw == "" or raw.startswith("#"):
        items = [(at, c) for at, c in content if not c.startswith("#")]
        if items and all(_is_item(c) for _, c in items):
            for at, item in items:
                problem = _item_problem(item[1:].strip())
                if problem:
                    problems.append((at, problem))
        return problems
    if raw[0] in "\"'[{":
        problem = _closed_problem("\n".join([raw] + [c for _, c in content]))
        if problem:
            problems.append((index, problem))
        return problems
    commented = False
    for number, (at, text) in enumerate([(index, raw)] + content):
        if text.startswith("#"):
            commented = True
            continue
        if commented:
            problems.append((at, "text after a comment inside a value"))
            break
        problem = _start_problem(text) if number == 0 else None
        if problem is None:
            problem, commented = _plain_problem(text)
        if problem:
            problems.append((at, problem))
            break
    return problems


def _start_problem(text: str) -> str | None:
    """Why an unquoted value cannot start like text, or None."""
    if text[0] in _RESERVED:
        return f'an unquoted value cannot start with "{text[0]}", quote it'
    if text[0] in "-?" and (len(text) == 1 or text[1] in " \t"):
        return f'"{text[0]} " inside a value starts a list or key, quote it'
    return None


def _plain_problem(text: str) -> tuple[str | None, bool]:
    """(problem or None, whether a comment ends text) for one plain line."""
    colon, comment = _COLON.search(text), _COMMENT.search(text)
    if colon and (comment is None or colon.start() < comment.start()):
        if colon.end() == len(text) and colon.group() == ":":
            return '":" at the end of an unquoted value, quote it', False
        return '": " inside an unquoted value, quote it', False
    return None, comment is not None


def _item_problem(text: str) -> str | None:
    """Why a block list item's text would be rejected, or None."""
    if not text:
        return None
    if text[0] in "\"'[{":
        return _closed_problem(text)
    problem = _start_problem(text)
    if problem:
        return problem
    colon, comment = _COLON.search(text), _COMMENT.search(text)
    if not colon or (comment is not None and comment.start() < colon.start()):
        return None
    rest = text[colon.end():].strip()
    if not rest or rest[0] in "#|>":
        return None
    if rest[0] in "\"'[{":
        return _closed_problem(rest)
    return _start_problem(rest) or _plain_problem(rest)[0]


def _closed_problem(text: str) -> str | None:
    """Why a quoted or flow value would be rejected, or None."""
    if text[0] in "\"'":
        end = _quote_close(text, 0)
        if end is None:
            return f"the {text[0]} quote is never closed"
    else:
        end = _flow_close(text)
        if end is None:
            return f'the "{text[0]}" is never closed'
    rest = text[end:].strip()
    if rest and not rest.startswith("#"):
        return f'text after the closing {text[end - 1]}, quote the whole value'
    return None


def _flow_close(text: str) -> int | None:
    """The index just past the flow collection that starts text, or None."""
    depth, i, lead = 0, 0, True
    while i < len(text):
        char = text[i]
        if char in "\"'" and lead:
            end = _quote_close(text, i)
            if end is None:
                return None
            i, lead = end, False
            continue
        if char in "[{":
            depth, lead = depth + 1, True
        elif char in "]}":
            depth, lead = depth - 1, False
            if depth == 0:
                return i + 1
        elif char in ",:":
            lead = True
        elif not char.isspace():
            lead = False
        i += 1
    return None


def _value(raw: str, children: list[str]):
    if raw in _BLOCK:
        return _block_scalar(raw, children)
    content = [c for c in children if c.strip()]
    if raw == "":
        if not content:
            return None
        if all(_is_item(c) for c in content):
            items = [_scalar(c.strip()[1:].strip()) for c in content]
            return [item for item in items if item is not None]
        return "\n".join(children)
    if content and raw[0] not in "\"'[{":
        raw = " ".join([raw] + [c.strip() for c in content])
    return _scalar(raw)


def _is_item(line: str) -> bool:
    stripped = line.strip()
    return stripped == "-" or stripped.startswith("- ")


def _block_scalar(indicator: str, children: list[str]) -> str:
    content = [c for c in children if c.strip()]
    indent = min((len(c) - len(c.lstrip()) for c in content), default=0)
    rows = [c[indent:] if c.strip() else "" for c in children]
    if indicator.startswith("|"):
        text = "\n".join(rows)
    else:
        parts, current = [], []
        for row in rows:
            if row:
                current.append(row)
            else:
                parts.append(" ".join(current))
                current = []
        parts.append(" ".join(current))
        text = "\n".join(parts)
    if indicator.endswith("-") or not text:
        return text
    return text + "\n"


def _scalar(text: str):
    text = text.strip()
    if not text:
        return None
    if text[0] in "\"'":
        return _unquote(text)
    if text.startswith("[["):
        return text
    if text[0] == "[":
        return _flow_list(text)
    if text[0] == "{":
        return text
    cut = text.find(" #")
    if cut != -1:
        text = text[:cut].rstrip()
    if text in ("", "~", "null", "Null", "NULL"):
        return None
    if text in ("true", "True", "TRUE"):
        return True
    if text in ("false", "False", "FALSE"):
        return False
    if _INT.match(text):
        return int(text)
    return text


def _unquote(text: str) -> str:
    """The content of a quoted scalar; an unquoted text comes back as is."""
    if not text or text[0] not in "\"'":
        return text
    quote, out, i = text[0], [], 1
    while i < len(text):
        char = text[i]
        if quote == '"' and char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if char == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                out.append("'")
                i += 2
                continue
            break
        out.append(char)
        i += 1
    return "".join(out)


def _flow_list(text: str) -> list:
    items, buf, depth, i = [], [], 0, 1
    while i < len(text):
        char = text[i]
        if char in "\"'" and not "".join(buf).strip():
            end = _quoted_end(text, i)
            buf.append(text[i:end])
            i = end
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            if depth == 0:
                break
            depth -= 1
        elif char == "," and depth == 0:
            items.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(char)
        i += 1
    items.append("".join(buf))
    values = [_scalar(item) for item in items]
    return [value for value in values if value is not None]


def _quoted_end(text: str, start: int) -> int:
    """The index just past the quoted scalar that starts at text[start]."""
    end = _quote_close(text, start)
    return len(text) if end is None else end


def _quote_close(text: str, start: int) -> int | None:
    """The index just past the quote opened at text[start]; None if open."""
    quote, i = text[start], start + 1
    while i < len(text):
        if quote == '"' and text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            if quote == "'" and text[i + 1:i + 2] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return None
