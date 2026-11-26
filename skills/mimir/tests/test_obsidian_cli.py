import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TESTS

import obsidian_cli

STUB = TESTS / "stub_obsidian.py"


class RunTest(unittest.TestCase):
    def test_stdin_is_devnull(self):
        # The test's own stdin becomes a pipe, so a child that inherits it
        # instead of getting the null device is seen.
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "stdin.log"
            read, write = os.pipe()
            saved = os.dup(0)
            try:
                os.dup2(read, 0)
                with mock.patch.dict(os.environ, {
                        "MIMIR_OBSIDIAN": str(STUB),
                        "MIMIR_STUB_STDIN_LOG": str(log),
                        "MIMIR_STUB_ERROR": "", "MIMIR_STUB_EXIT": "",
                        "MIMIR_STUB_STDERR_ERROR": ""}):
                    obsidian_cli.run(["version"])
            finally:
                os.dup2(saved, 0)
                for fd in (saved, read, write):
                    os.close(fd)
            self.assertEqual(log.read_text().split(), ["devnull"])

    def test_error_on_stderr_at_exit_zero_raises(self):
        with mock.patch.dict(os.environ, {"MIMIR_OBSIDIAN": str(STUB),
                                          "MIMIR_STUB_ERROR": "",
                                          "MIMIR_STUB_STDERR_ERROR": "move"}):
            with self.assertRaisesRegex(obsidian_cli.CliError, "Error:"):
                obsidian_cli.run(["move", "path=a.md", "to=b.md"])
            self.assertIn("stub", obsidian_cli.run(["version"]))


class CheckVaultTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.vault = Path(tmp.name) / "Alice vault"
        self.vault.mkdir()
        patcher = mock.patch.dict(os.environ, {
            "MIMIR_OBSIDIAN": str(STUB), "MIMIR_STUB_VAULT": "",
            "MIMIR_STUB_OPEN_VAULT": "", "MIMIR_STUB_ERROR": "",
            "MIMIR_STUB_STDERR_ERROR": "", "MIMIR_STUB_EXIT": ""})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_open_vault_matches(self):
        with mock.patch.dict(os.environ,
                             {"MIMIR_STUB_OPEN_VAULT": str(self.vault)}):
            obsidian_cli.check_vault(self.vault)

    def test_other_open_vault_raises_naming_both(self):
        other = self.vault.parent / "Bob vault"
        with mock.patch.dict(os.environ, {"MIMIR_STUB_OPEN_VAULT": str(other)}):
            with self.assertRaises(obsidian_cli.WrongVault) as caught:
                obsidian_cli.check_vault(self.vault)
        self.assertIn(str(other), str(caught.exception))
        self.assertIn(str(self.vault), str(caught.exception))
        self.assertNotIsInstance(caught.exception, obsidian_cli.CliError)

    def test_cli_failure_is_a_cli_error(self):
        with mock.patch.dict(os.environ, {"MIMIR_STUB_ERROR": "vault"}):
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.check_vault(self.vault)


if __name__ == "__main__":
    unittest.main()
