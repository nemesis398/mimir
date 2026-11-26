"""Read and validate a vault's ``.mimir.json``.

The top-level keys are the ``Config`` field names, and every one of them is
required. ``kinds`` maps a kind name to an object with the ``Kind`` fields
other than ``name``; only ``hub`` and ``has_status`` have no default there.
Any problem raises ``ConfigError`` with a message that names the key.
"""

import json
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Literal

CONFIG_NAME = ".mimir.json"
PROGRESS_MODES = ("checkboxes",)


class ConfigError(Exception):
    """The config file is missing, unreadable or breaks the schema."""


@dataclass(frozen=True)
class Kind:
    name: str
    note_type: str
    has_status: bool
    required: tuple[str, ...]
    hub: bool
    progress: Literal["checkboxes"] | None
    template: str | None
    folder: str | None


@dataclass(frozen=True)
class Config:
    areas: tuple[str, ...]
    system_folders: tuple[str, ...]
    root_allowlist: tuple[str, ...]
    states: tuple[str, ...]
    states_by_type: dict[str, tuple[str, ...]]
    kinds: dict[str, Kind]
    view_properties: frozenset[str]
    allowed_keys: frozenset[str]
    size_cap_bytes: int
    size_skip_folders: tuple[str, ...]
    size_skip_types: frozenset[str]
    size_skip_names: frozenset[str]


def load(vault: Path) -> Config:
    """The config in ``vault/.mimir.json``."""
    return load_file(Path(vault) / CONFIG_NAME)


def load_file(path: Path) -> Config:
    """The config in the JSON file at path."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{CONFIG_NAME}: not found at {path}") from None
    except (OSError, UnicodeDecodeError) as err:
        raise ConfigError(f"{CONFIG_NAME}: cannot read {path}: {err}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as err:
        raise ConfigError(f"{CONFIG_NAME}: invalid JSON: {err}") from None
    return from_dict(data)


def from_dict(data) -> Config:
    """Validate a parsed config object and build the Config."""
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_NAME}: the top level must be an object")
    names = [f.name for f in fields(Config)]
    for key in data:
        if key not in names:
            raise ConfigError(f"{key}: unknown key")
    for key in names:
        if key not in data:
            raise ConfigError(f"{key}: missing key")
    size_cap = data["size_cap_bytes"]
    if isinstance(size_cap, bool) or not isinstance(size_cap, int) \
            or size_cap <= 0:
        raise ConfigError("size_cap_bytes: must be a positive integer")
    return Config(
        areas=_strings(data, "areas"),
        system_folders=_strings(data, "system_folders"),
        root_allowlist=_strings(data, "root_allowlist"),
        states=_strings(data, "states"),
        states_by_type=_states_by_type(data["states_by_type"]),
        kinds=_kinds(data["kinds"]),
        view_properties=frozenset(_strings(data, "view_properties")),
        allowed_keys=frozenset(_strings(data, "allowed_keys")),
        size_cap_bytes=size_cap,
        size_skip_folders=_strings(data, "size_skip_folders"),
        size_skip_types=frozenset(_strings(data, "size_skip_types")),
        size_skip_names=frozenset(_strings(data, "size_skip_names")),
    )


def _string_list(value, key: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value):
        raise ConfigError(f"{key}: must be a list of strings")
    return tuple(value)


def _strings(data: dict, key: str) -> tuple[str, ...]:
    return _string_list(data[key], key)


def _states_by_type(value) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ConfigError("states_by_type: must be an object")
    return {name: _string_list(states, f"states_by_type.{name}")
            for name, states in value.items()}


def _kinds(value) -> dict[str, Kind]:
    if not isinstance(value, dict):
        raise ConfigError("kinds: must be an object")
    return {name: _kind(name, spec) for name, spec in value.items()}


def _kind(name: str, spec) -> Kind:
    prefix = f"kinds.{name}"
    if not isinstance(spec, dict):
        raise ConfigError(f"{prefix}: must be an object")
    allowed = [f.name for f in fields(Kind) if f.name != "name"]
    for key in spec:
        if key not in allowed:
            raise ConfigError(f"{prefix}.{key}: unknown key")
    for key in ("has_status", "hub"):
        if key not in spec:
            raise ConfigError(f"{prefix}.{key}: missing key")
        if not isinstance(spec[key], bool):
            raise ConfigError(f"{prefix}.{key}: must be true or false")
    progress = spec.get("progress")
    if progress is not None and progress not in PROGRESS_MODES:
        raise ConfigError(f'{prefix}.progress: must be "checkboxes" or null')
    for key in ("note_type", "template", "folder"):
        value = spec.get(key)
        if value is not None and (not isinstance(value, str) or not value):
            raise ConfigError(f"{prefix}.{key}: must be a non-empty string")
    return Kind(
        name=name,
        note_type=spec.get("note_type") or name,
        has_status=spec["has_status"],
        required=_string_list(spec.get("required", []), f"{prefix}.required"),
        hub=spec["hub"],
        progress=progress,
        template=spec.get("template"),
        folder=spec.get("folder"),
    )
