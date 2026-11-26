"""Read the notes of a vault and their frontmatter.

The frontmatter reader is a small YAML subset, enough for Obsidian
properties: plain, quoted and block scalars, flow and block lists. A nested
map (an indented block under an empty key) is passed through as its raw
text. An unquoted value that starts with ``[[`` stays a string, so that
``related: [[A]]`` yields its link instead of a nested list.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

_KEY = re.compile(r"^([^\s#\-][^:]*?):(?:[ \t]+(.*?))?[ \t]*$")
_INT = re.compile(r"^[-+]?(?:0|[1-9][0-9]*)$")
_BLOCK = {"|", "|-", "|+", ">", ">-", ">+"}
_LINK = re.compile(r"\[\[([^\[\]|#]*)(?:#[^\[\]|]*)?(?:\|[^\[\]]*)?\]\]")


@dataclass(frozen=True)
class Note:
    path: Path
    rel: str
    front: dict | None
    body: str
    size: int


def parse_front(text: str) -> tuple[dict | None, str]:
    """Split text into (frontmatter dict, body); (None, text) without one."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None, text
    for end in range(1, len(lines)):
        if lines[end].rstrip("\r\n").rstrip() == "---":
            inner = [line.rstrip("\r\n") for line in lines[1:end]]
            return _parse_map(inner), "".join(lines[end + 1:])
    return None, text


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


def _parse_map(lines: list[str]) -> dict:
    result = {}
    i, count = 0, len(lines)
    while i < count:
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or _indented(line):
            continue
        match = _KEY.match(line)
        if not match:
            continue
        key = _unquote(match.group(1).strip())
        raw = (match.group(2) or "").strip()
        children = []
        while i < count:
            nxt = lines[i]
            if _indented(nxt) or not nxt.strip() or (
                    raw == "" and (nxt == "-" or nxt.startswith("- "))):
                children.append(nxt)
                i += 1
            else:
                break
        while children and not children[-1].strip():
            children.pop()
        result[key] = _value(raw, children)
    return result


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
    return len(text)
