import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import SCRIPTS, TESTS, VaultTestCase, config_data, make_config

import new
import obsidian_cli

STUB = TESTS / "stub_obsidian.py"

PROJECT_TEMPLATE = """---
type: project
status: planned
description:
created: {{date}}
---

# {{name}}
"""


class NewNoteTest(VaultTestCase):
    def setUp(self):
        super().setUp()
        data = config_data()
        data["kinds"]["project"]["template"] = "templates/Project.md"
        data["allowed_keys"].append("created")
        self.cfg = make_config(data)
        self.log = self.vault.parent / f"{self.vault.name}.log"
        self.cwd_log = self.vault.parent / f"{self.vault.name}.cwd"
        self.addCleanup(lambda: self.log.unlink(missing_ok=True))
        self.addCleanup(lambda: self.cwd_log.unlink(missing_ok=True))
        env = {"MIMIR_OBSIDIAN": str(STUB), "MIMIR_STUB_LOG": str(self.log),
               "MIMIR_STUB_CWD_LOG": str(self.cwd_log),
               "MIMIR_STUB_VAULT": str(self.vault),
               "MIMIR_STUB_ERROR": "", "MIMIR_STUB_EXIT": ""}
        for patcher in (mock.patch.dict(os.environ, env),
                        mock.patch.object(obsidian_cli, "REREAD_DELAY", 0)):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.write({"templates/Project.md": PROJECT_TEMPLATE})

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(l) for l in
                self.log.read_text(encoding="utf-8").splitlines()]

    def test_new_project_layout(self):
        path = new.new_note(self.vault, self.cfg, "project", "Pantry",
                            "Workshop")
        self.assertEqual(path, self.vault / "Workshop/Pantry/Pantry.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("type: project\nstatus: planned\n", text)
        self.assertIn("# Pantry", text)
        self.assertNotIn("{{", text)
        self.assertRegex(text, r"created: \d{4}-\d\d-\d\d\n")

    def test_non_hub_kind_uses_folder(self):
        path = new.new_note(self.vault, self.cfg, "plan", "Backup", "AI")
        self.assertEqual(path, self.vault / "AI/plans/Backup.md")
        self.assertIn("type: plan", path.read_text(encoding="utf-8"))

    def test_parent_sets_project_link(self):
        path = new.new_note(self.vault, self.cfg, "plan", "Backup", "AI",
                            parent="Pantry")
        self.assertIn('project: "[[Pantry]]"\n',
                      path.read_text(encoding="utf-8"))

    def test_cli_runs_in_the_vault(self):
        new.new_note(self.vault, self.cfg, "project", "Pantry", "Workshop")
        cwds = self.cwd_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(cwds), 2)
        self.assertEqual({os.path.realpath(c) for c in cwds},
                         {os.path.realpath(self.vault)})

    def test_crlf_template_keeps_crlf(self):
        (self.vault / "templates/Project.md").write_bytes(
            PROJECT_TEMPLATE.replace("\n", "\r\n").encode("utf-8"))
        path = new.new_note(self.vault, self.cfg, "project", "Pantry",
                            "Workshop", parent="Garden")
        data = path.read_bytes()
        self.assertIn(b'project: "[[Garden]]"\r\n', data)
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))

    def test_parent_without_frontmatter_raises(self):
        self.write({"templates/Project.md": "# {{name}}\n"})
        with self.assertRaises(new.NewError):
            new.new_note(self.vault, self.cfg, "project", "Pantry",
                         "Workshop", parent="Garden")
        self.assertEqual(self.calls(), [])

    def test_name_taken_anywhere(self):
        self.write({"AI/x/Pantry.md": "x\n"})
        with self.assertRaises(new.NameTaken):
            new.new_note(self.vault, self.cfg, "project", "Pantry",
                         "Workshop")
        self.assertEqual(self.calls(), [])

    def test_unknown_area_and_kind(self):
        with self.assertRaises(new.UnknownArea):
            new.new_note(self.vault, self.cfg, "project", "X", "Nope")
        with self.assertRaises(new.UnknownKind):
            new.new_note(self.vault, self.cfg, "nope", "X", "AI")

    def test_new_keeps_unicode_name(self):
        name = "Pantry-Umbau – grön"
        path = new.new_note(self.vault, self.cfg, "project", name, "Workshop")
        self.assertEqual(path.relative_to(self.vault).as_posix(),
                         f"Workshop/{name}/{name}.md")
        self.assertIn(f"# {name}", path.read_text(encoding="utf-8"))
        self.assertIn(f"create path=Workshop/{name}/{name}.md",
                      " ".join(self.calls()[-1]))

    def test_new_stops_on_cli_error(self):
        with mock.patch.dict(os.environ, {"MIMIR_STUB_ERROR": "create"}):
            with self.assertRaises(new.NewError):
                new.new_note(self.vault, self.cfg, "project", "Pantry",
                             "Workshop")
        self.assertEqual([c[0] for c in self.calls()], ["version", "create"])
        self.assertFalse((self.vault / "Workshop/Pantry/Pantry.md")
                         .exists())

    def test_template_filled_first_raises(self):
        def templater(_seconds):
            (self.vault / "Workshop/Pantry/Pantry.md").write_text(
                "template text\n", encoding="utf-8")

        with mock.patch.object(obsidian_cli.time, "sleep", templater):
            with self.assertRaises(new.NewError):
                new.new_note(self.vault, self.cfg, "project", "Pantry",
                             "Workshop")


class ScriptTest(unittest.TestCase):
    def test_scripts_run_from_another_directory(self):
        for name in ("check.py", "new.py", "split.py"):
            with self.subTest(script=name):
                script = SCRIPTS / name
                self.assertTrue(script.read_text(encoding="utf-8")
                                .startswith("#!/usr/bin/env python3\n"))
                self.assertTrue(os.access(script, os.X_OK))
                with tempfile.TemporaryDirectory() as cwd:
                    done = subprocess.run(
                        [sys.executable, "-I", "-B", str(script), "--help"],
                        cwd=cwd, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == "__main__":
    unittest.main()
