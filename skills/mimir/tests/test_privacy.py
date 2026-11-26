import os
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
SKIP_DIRS = {".git", ".claude-plugin"}
SKIP_FILES = {"README.md"}


class PrivacyTest(unittest.TestCase):
    def test_no_private_strings(self):
        found = []
        for root, dirs, files in os.walk(REPO):
            rel_root = os.path.relpath(root, REPO)
            if rel_root == ".":
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if rel_root == "." and name in SKIP_FILES:
                    continue
                path = os.path.join(root, name)
                with open(path, "rb") as handle:
                    data = handle.read()
                for word in PRIVATE:
                    if word.encode("utf-8") in data:
                        found.append(f"{os.path.relpath(path, REPO)}: {word}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
