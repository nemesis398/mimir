import os
import subprocess
import unittest

from helpers import REPO

# Assembled from pieces so that this file does not match itself.
PRIVATE = [
    "ali" + "ce-home",
    "example" + "family",
    "bob" + "knows",
    "carol" + "77",
    "Private" + "-Notes",
]
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
        found = []
        for rel in repo_files():
            if rel in SKIP_FILES or rel.startswith(SKIP_DIRS):
                continue
            path = os.path.join(REPO, rel)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as handle:
                data = handle.read()
            for word in PRIVATE:
                if word.encode("utf-8") in data:
                    found.append(f"{rel}: {word}")
        self.assertEqual(found, [])

    def test_ignored_files_skipped(self):
        self.assertFalse(any("__pycache__" in rel for rel in repo_files()))


if __name__ == "__main__":
    unittest.main()
