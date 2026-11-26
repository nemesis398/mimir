import os
import shutil
import subprocess
import unittest

from helpers import REPO

WORDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "private-words.txt")


def private_words() -> list[str]:
    """Guarded strings from MIMIR_PRIVATE_WORDS (comma-separated) or the
    ignored tests/private-words.txt (one per line); none are kept in the repo."""
    words = [w.strip() for w in os.environ.get("MIMIR_PRIVATE_WORDS", "").split(",")]
    if os.path.isfile(WORDS_FILE):
        with open(WORDS_FILE, encoding="utf-8") as handle:
            words += [line.strip() for line in handle]
    return [w for w in words if w]


SKIP_DIRS = (".git/", ".claude-plugin/")
SKIP_FILES = {"README.md"}


def repo_files() -> list[str]:
    """The tracked files and the untracked ones git does not ignore."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-z", "--cached", "--others",
         "--exclude-standard"],
        check=True, capture_output=True).stdout
    return sorted({name.decode("utf-8") for name in out.split(b"\0") if name})


class PrivacyTest(unittest.TestCase):
    def test_no_private_strings(self):
        words = private_words()
        if not words:
            self.skipTest("no MIMIR_PRIVATE_WORDS and no tests/private-words.txt")
        if shutil.which("git") is None:
            self.skipTest("git not available")
        found = []
        for rel in repo_files():
            if rel in SKIP_FILES or rel.startswith(SKIP_DIRS):
                continue
            path = os.path.join(REPO, rel)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            for word in words:
                if word.encode("utf-8") in data:
                    found.append(f"{rel}: {word}")
        self.assertEqual(found, [])

    def test_ignored_files_skipped(self):
        self.assertFalse(any("__pycache__" in rel for rel in repo_files()))


if __name__ == "__main__":
    unittest.main()
