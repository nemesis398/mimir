"""Shared test helpers: the repo paths and a fixture vault builder."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / "scripts"
REPO = TESTS.parent.parent.parent
EXAMPLE_CONFIG = REPO / "config.example.json"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def write_vault(root: Path, files: dict) -> Path:
    """Write each relative path to its content (str or bytes) under root."""
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_bytes(content.encode("utf-8"))
    return root


class VaultTestCase(unittest.TestCase):
    """A test case with a fresh, empty vault directory in self.vault."""

    def setUp(self):
        self.vault = Path(tempfile.mkdtemp(prefix="mimir-test-"))
        self.addCleanup(shutil.rmtree, self.vault, True)

    def write(self, files: dict) -> Path:
        return write_vault(self.vault, files)


def config_data() -> dict:
    """A fresh config object for the checker tests, Alice's made-up vault."""
    return {
        "areas": ["Workshop", "Computer", "AI"],
        "system_folders": ["templates", "attachments"],
        "root_allowlist": ["Home.md", "*.base"],
        "states": ["planned", "active", "paused", "done", "cancelled"],
        "states_by_type": {"computer": ["stale"]},
        "kinds": {
            "project": {
                "has_status": True,
                "required": ["description"],
                "hub": True,
                "progress": None,
                "template": None,
                "folder": None,
            },
            "plan": {
                "has_status": True,
                "required": ["description"],
                "hub": False,
                "progress": "checkboxes",
                "template": None,
                "folder": "plans",
            },
        },
        "view_properties": ["type", "status", "domain", "updated",
                            "description", "progress", "project"],
        "allowed_keys": ["created", "machine", "tags", "aliases"],
        "size_cap_bytes": 40960,
        "size_skip_folders": ["AI/specs"],
        "size_skip_types": ["transaktion"],
        "size_skip_names": ["*.excalidraw.md"],
    }


def make_config(data: dict | None = None):
    """The Config built from data, by default from config_data()."""
    import config
    return config.from_dict(config_data() if data is None else data)
