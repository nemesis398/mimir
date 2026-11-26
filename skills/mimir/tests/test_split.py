import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import TESTS, VaultTestCase

import obsidian_cli
import split

STUB = TESTS / "stub_obsidian.py"

CONFIG = {
    "areas": ["Garden", "Work"],
    "system_folders": ["templates", "attachments"],
    "root_allowlist": ["Home.md"],
    "states": ["planned", "active", "paused", "done", "cancelled"],
    "states_by_type": {},
    "kinds": {
        "plan": {"has_status": True, "hub": False, "progress": "checkboxes"},
    },
    "view_properties": ["type", "status", "description", "progress"],
    "allowed_keys": ["tags", "aliases"],
    "size_cap_bytes": 150,
    "size_skip_folders": ["AI/specs"],
    "size_skip_types": ["recipe"],
    "size_skip_names": ["*.excalidraw.md", "Seeds – Deck"],
}

TASK_PLAN = """---
type: plan
status: done
progress: 50
description: "Alice's garden plan"
---
# Garden plan

Goal: a garden that Alice can keep.

## Global Constraints

- No pesticides.

## Task 1: Beds (build)

- [x] **Step 1:** Dig.

```python
# /tmp/scratch-dir/check_run.py
## not a heading either
- [ ] not a checkbox
```

- [ ] **Step 2:** Plant.

## Task 2: Water/Paths

- [-] Lay the hose.
"""

PHASE_PLAN = """---
type: plan
status: done
---
# Work plan

## Phase 1: Setup

Intro to the phase.

### Task 1: Desk

- [x] Buy it.

### Task 2: Chair

- [x] Buy it.

## Phase 2: Use

### Task 3: Sit

- [ ] Sit down.
"""

ENTITY = """---
type: computer
status: active
tags: [laptop]
---
Alice's laptop.

## Decisions

- Oak, not pine.

## Troubleshooting

### Wifi drops

Reload the module.

## Links

See [[Home]]."""


KEEP_PLAN = """---
type: plan
status: done
---
# Pond plan

## Global Constraints

- Alice keeps the frogs.

## Review Focus

- The liner.

## Phase 1: Dig

### Task 1: Hole

- [x] Dig it.

## Phase 2: Fill

### Task 2: Water

- [ ] Fill it.
"""


def snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


class SplitTextTest(unittest.TestCase):
    def test_round_trip_real_shapes(self):
        for name, text in (("Garden plan", TASK_PLAN),
                           ("Work plan", PHASE_PLAN),
                           ("Garden Shed", ENTITY)):
            with self.subTest(name=name):
                hub, parts = split.split_text(name, text)
                self.assertGreater(len(parts), 1)
                self.assertEqual(split.join_parts(hub, parts), text)
                for basename, _ in parts:
                    self.assertIn(f"[[{basename}]]", hub)

    def test_top_level_picked_and_forced(self):
        _, parts = split.split_text("Work plan", PHASE_PLAN)
        self.assertEqual([b for b, _ in parts],
                         ["Work plan – Phase 1 Setup", "Work plan – Phase 2 Use"])
        hub, parts = split.split_text("Work plan", PHASE_PLAN, level=3)
        self.assertEqual([b for b, _ in parts],
                         ["Work plan – Task 1 Desk", "Work plan – Task 2 Chair",
                          "Work plan – Phase 2 Use", "Work plan – Task 3 Sit"])
        self.assertIn("Intro to the phase.\n", hub)
        self.assertEqual(split.join_parts(hub, parts), PHASE_PLAN)

    def test_shallower_heading_ends_a_deeper_part(self):
        text = ("# Plan\n\n## Review Focus\n\n- one\n\n### Task 1: A\n\na\n\n"
                "### Task 2: B\n\nb\n\n## Rulings\n\nr\n")
        hub, parts = split.split_text("Plan", text, level=3)
        self.assertEqual([b for b, _ in parts],
                         ["Plan – Task 1 A", "Plan – Task 2 B", "Plan – Rulings"])
        self.assertEqual(parts[1][1], "### Task 2: B\n\nb\n\n")
        self.assertIn("## Review Focus\n\n- one\n", hub)
        self.assertEqual(split.join_parts(hub, parts), text)

    def test_hub_keeps_frontmatter_and_preamble(self):
        hub, parts = split.split_text("Garden Shed", ENTITY)
        self.assertTrue(hub.startswith("---\ntype: computer\n"))
        self.assertIn("Alice's laptop.\n", hub)
        self.assertIn("## Parts\n", hub)
        self.assertNotIn("Oak", hub)
        self.assertTrue(parts[0][1].startswith("## Decisions\n"))

    def test_fence_heading_not_split(self):
        text = ("# Probe\n\n## A\n\n```sh\n# /tmp/scratch-dir/check_run.py\n"
                "# second\n```\n\n## B\n\nend\n")
        hub, parts = split.split_text("Probe", text)
        self.assertEqual([b for b, _ in parts], ["Probe – A", "Probe – B"])
        self.assertIn("# /tmp/scratch-dir/check_run.py\n", parts[0][1])
        self.assertEqual(split.join_parts(hub, parts), text)
        _, parts = split.split_text("Garden plan", TASK_PLAN)
        self.assertEqual(len(parts), 3)

    def test_part_names_keep_en_dash_and_umlaut(self):
        name = "Pantry-Umbau – Overview"
        text = "Intro\n\n## grön: Übersicht/Plan\n\nä\n\n## Größe\n\nö\n"
        hub, parts = split.split_text(name, text)
        self.assertEqual(
            [b for b, _ in parts],
            ["Pantry-Umbau – Overview – grön Übersicht Plan",
             "Pantry-Umbau – Overview – Größe"])
        self.assertIn("[[Pantry-Umbau – Overview – Größe]]", hub)
        self.assertEqual(split.join_parts(hub, parts), text)

    def test_keep_leaves_leading_sections_in_hub(self):
        keep = ("Global Constraints", "Review Focus")
        hub, parts = split.split_text("Pond plan", KEEP_PLAN, keep=keep)
        self.assertEqual([b for b, _ in parts],
                         ["Pond plan – Phase 1 Dig", "Pond plan – Phase 2 Fill"])
        self.assertIn("## Global Constraints\n\n- Alice keeps the frogs.\n",
                      hub)
        self.assertIn("## Review Focus\n\n- The liner.\n", hub)
        self.assertLess(hub.index("## Review Focus"), hub.index("## Parts"))
        self.assertEqual(split.join_parts(hub, parts), KEEP_PLAN)

    def test_plan_keep_is_everything_before_the_first_phase(self):
        self.assertEqual(split.plan_keep(KEEP_PLAN),
                         ("Global Constraints", "Review Focus"))
        self.assertEqual(split.plan_keep(PHASE_PLAN), ())
        self.assertEqual(split.plan_keep(TASK_PLAN), ("Global Constraints",))

    def test_keep_must_lead_and_exist(self):
        for keep in (("Phase 2: Fill",), ("Nowhere",),
                     ("Global Constraints", "Review Focus", "Phase 1: Dig",
                      "Phase 2: Fill")):
            with self.subTest(keep=keep):
                with self.assertRaises(split.SplitError):
                    split.split_text("Pond plan", KEEP_PLAN, keep=keep)

    def test_duplicate_headings_get_distinct_parts(self):
        text = "## Notes\n\na\n\n## Notes\n\nb\n"
        hub, parts = split.split_text("X", text)
        self.assertEqual([b for b, _ in parts], ["X – Notes", "X – Notes 2"])
        self.assertEqual(split.join_parts(hub, parts), text)


class HeadingLinkTest(VaultTestCase):
    def test_piped_heading_link_rewritten(self):
        self.write({
            "Computer/Garden Shed.md": ENTITY,
            "Other.md": "Try [[Garden Shed#Troubleshooting|the fix]].\n",
            "Plain.md": "See [[Garden Shed]].\n",
        })
        found = split.heading_link_rewrites(
            self.vault, "Garden Shed",
            {"Troubleshooting": "Garden Shed – Troubleshooting"})
        self.assertEqual(found, [(
            self.vault / "Other.md",
            "[[Garden Shed#Troubleshooting|the fix]]",
            "[[Garden Shed – Troubleshooting|the fix]]")])

    def test_subheading_and_path_links_keep_their_shape(self):
        self.write({
            "a.md": "![[Computer/Garden Shed#Wifi drops]] "
                    "[[Garden Shed#Nowhere]]\n"
                    "```\n[[Garden Shed#Decisions]]\n```\n",
        })
        found = split.heading_link_rewrites(
            self.vault, "Garden Shed",
            {"Wifi drops": "Garden Shed – Troubleshooting#Wifi drops",
             "Decisions": "Garden Shed – Decisions"})
        self.assertEqual([(old, new) for _, old, new in found], [(
            "[[Computer/Garden Shed#Wifi drops]]",
            "[[Computer/Garden Shed – Troubleshooting#Wifi drops]]")])


class RewriteTest(VaultTestCase):
    def test_rewrite_keeps_crlf(self):
        self.write({"Other.md": ""})
        other = self.vault / "Other.md"
        other.write_bytes(b"a\r\n[[Garden Shed#Decisions]]\r\nb\r\n")
        rewrites = split.heading_link_rewrites(
            self.vault, "Garden Shed",
            {"Decisions": "Garden Shed – Decisions"})
        split.apply_rewrites(rewrites)
        self.assertEqual(other.read_bytes(),
                         "a\r\n[[Garden Shed – Decisions]]\r\nb\r\n"
                         .encode("utf-8"))


class CliTest(unittest.TestCase):
    def test_error_line_at_exit_zero_raises(self):
        with mock.patch.dict(os.environ, {"MIMIR_OBSIDIAN": str(STUB),
                                          "MIMIR_STUB_ERROR": "create"}):
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.run(["create", "path=x.md"])
            self.assertIn("stub", obsidian_cli.run(["version"]))

    def test_create_retries_the_folder_race(self):
        race = obsidian_cli.CliError(
            "obsidian create path=a/b.md: Error: Folder already exists.")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(obsidian_cli, "RETRY_DELAY", 0), \
                mock.patch.object(obsidian_cli, "REREAD_DELAY", 0), \
                mock.patch.object(obsidian_cli, "run",
                                  side_effect=[race, "Created"]) as run:
            obsidian_cli.create_note(Path(tmp), "a/b.md", "text\n")
            self.assertEqual(run.call_count, 2)
            self.assertEqual((Path(tmp) / "a/b.md").read_text(), "text\n")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(obsidian_cli, "RETRY_DELAY", 0), \
                mock.patch.object(obsidian_cli, "run",
                                  side_effect=[race] * 4) as run:
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.create_note(Path(tmp), "a/b.md", "text\n")
            self.assertEqual(run.call_count, 4)
        other = obsidian_cli.CliError("obsidian create path=x: Error: nope")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(obsidian_cli, "run",
                                  side_effect=[other]) as run:
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.create_note(Path(tmp), "a/b.md", "text\n")
            self.assertEqual(run.call_count, 1)

    def test_run_takes_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cwd.log"
            with mock.patch.dict(os.environ, {"MIMIR_OBSIDIAN": str(STUB),
                                              "MIMIR_STUB_CWD_LOG": str(log),
                                              "MIMIR_STUB_ERROR": ""}):
                obsidian_cli.run(["version"], cwd=Path(tmp))
            self.assertEqual(os.path.realpath(log.read_text().strip()),
                             os.path.realpath(tmp))

    def test_nonzero_exit_and_missing_binary_raise(self):
        with mock.patch.dict(os.environ, {"MIMIR_OBSIDIAN": str(STUB),
                                          "MIMIR_STUB_EXIT": "version"}):
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.run(["version"])
        with mock.patch.dict(os.environ,
                             {"MIMIR_OBSIDIAN": "/nonexistent/obsidian"}):
            with self.assertRaises(obsidian_cli.CliError):
                obsidian_cli.run(["version"])


class SplitCommandTest(VaultTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(obsidian_cli, "REREAD_DELAY", 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.log = self.vault.parent / f"{self.vault.name}.log"
        self.cwd_log = self.vault.parent / f"{self.vault.name}.cwd"
        self.addCleanup(lambda: self.log.unlink(missing_ok=True))
        self.addCleanup(lambda: self.cwd_log.unlink(missing_ok=True))
        self.write({".mimir.json": json.dumps(CONFIG)})
        self.env = {"MIMIR_OBSIDIAN": str(STUB),
                    "MIMIR_STUB_LOG": str(self.log),
                    "MIMIR_STUB_CWD_LOG": str(self.cwd_log),
                    "MIMIR_STUB_VAULT": str(self.vault),
                    "MIMIR_STUB_OPEN_VAULT": "", "MIMIR_STUB_MOVE_APPEND": "",
                    "MIMIR_STUB_ERROR": "", "MIMIR_STUB_EXIT": "",
                    "MIMIR_STUB_STDERR_ERROR": ""}

    def run_split(self, *args, **env):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {**self.env, **env}), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(out):
            code = split.main([*args, "--vault", str(self.vault)])
        return code, out.getvalue()

    def calls(self) -> list:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines()]

    def read_split(self, folder: str, name: str):
        hub = (self.vault / folder / f"{name}.md").read_bytes().decode()
        parts = []
        for line in hub.split("## Parts\n", 1)[1].splitlines():
            if line.startswith("- [["):
                base = line[4:-2]
                text = (self.vault / folder / f"{base}.md").read_bytes()
                parts.append((base, text.decode("utf-8")))
        return hub, parts

    def test_real_run_creates_parts_and_rewrites(self):
        self.write({"Computer/Garden Shed.md": ENTITY,
                    "Other.md": "[[Garden Shed#Troubleshooting|the fix]] "
                                "[[Garden Shed#Wifi drops]]\n"})
        code, out = self.run_split("Computer/Garden Shed.md")
        self.assertEqual(code, 0, out)
        self.assertFalse((self.vault / "Computer/Garden Shed.md").exists())
        hub, parts = self.read_split("Computer/Garden Shed", "Garden Shed")
        self.assertEqual(split.join_parts(hub, parts), ENTITY)
        self.assertEqual(
            (self.vault / "Other.md").read_text("utf-8"),
            "[[Garden Shed – Troubleshooting|the fix]] "
            "[[Garden Shed – Troubleshooting#Wifi drops]]\n")
        self.assertIn("Other.md", out)
        calls = self.calls()
        self.assertEqual(calls[:3], [
            ["version"], ["vault", "info=path"],
            ["move", "path=Computer/Garden Shed.md",
             "to=Computer/Garden Shed/Garden Shed.md"]])
        self.assertEqual(calls[3], [
            "create", "path=Computer/Garden Shed/Garden Shed – Decisions.md"])
        self.assertEqual(sorted(p.name for p in
                                (self.vault / "Computer").iterdir()),
                         ["Garden Shed"])

    def test_cli_runs_in_the_vault(self):
        self.write({"Work plan.md": PHASE_PLAN})
        code, out = self.run_split("Work plan.md", "--force")
        self.assertEqual(code, 0, out)
        cwds = self.cwd_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(cwds), len(self.calls()))
        self.assertEqual({os.path.realpath(c) for c in cwds},
                         {os.path.realpath(self.vault)})

    def test_note_in_its_own_folder_is_not_moved(self):
        self.write({"Garden/Work plan/Work plan.md": PHASE_PLAN})
        code, out = self.run_split("Work plan", "--force")
        self.assertEqual(code, 0, out)
        self.assertNotIn("move", [c[0] for c in self.calls()])
        hub, parts = self.read_split("Garden/Work plan", "Work plan")
        front = "---\ntype: plan-part\nstatus: done\n---\n"
        bare = [(b, part.removeprefix(front)) for b, part in parts]
        self.assertEqual(split.join_parts(hub, bare), PHASE_PLAN)

    def test_move_error_stops(self):
        self.write({"Work plan.md": PHASE_PLAN})
        before = snapshot(self.vault)
        code, out = self.run_split("Work plan.md", "--force",
                                   MIMIR_STUB_ERROR="move")
        self.assertNotEqual(code, 0)
        self.assertEqual([c[0] for c in self.calls()],
                         ["version", "vault", "move"])
        self.assertEqual(snapshot(self.vault), before)

    def test_plan_keeps_leading_sections(self):
        self.write({"Pond plan.md": KEEP_PLAN})
        code, out = self.run_split("Pond plan.md", "--force")
        self.assertEqual(code, 0, out)
        hub, parts = self.read_split("Pond plan", "Pond plan")
        self.assertIn("## Global Constraints\n", hub)
        self.assertIn("## Review Focus\n", hub)
        self.assertEqual([b for b, _ in parts],
                         ["Pond plan – Phase 1 Dig", "Pond plan – Phase 2 Fill"])
        front = "---\ntype: plan-part\nstatus: done\n---\n"
        for _, part in parts:
            self.assertTrue(part.startswith(front + "## Phase "), part)
        bare = [(b, part[len(front):]) for b, part in parts]
        self.assertEqual(split.join_parts(hub, bare), KEEP_PLAN)

    def test_plan_part_split_deeper_keeps_the_part_front(self):
        front = "---\ntype: plan-part\nstatus: done\n---\n"
        text = front + "## Phase 1\n\n### Task 1: A\n\na\n\n### Task 2: B\n\nb\n"
        self.write({"Plan – Phase 1.md": text})
        code, out = self.run_split("Plan – Phase 1.md", "--force")
        self.assertEqual(code, 0, out)
        hub, parts = self.read_split("Plan – Phase 1", "Plan – Phase 1")
        self.assertTrue(hub.startswith(front))
        self.assertEqual(len(parts), 2)
        for _, part in parts:
            self.assertTrue(part.startswith(front + "### Task "), part)

    def test_keep_flag(self):
        self.write({"Garden Shed.md": ENTITY})
        code, out = self.run_split("Garden Shed.md", "--force",
                                   "--keep", "Decisions")
        self.assertEqual(code, 0, out)
        hub, parts = self.read_split("Garden Shed", "Garden Shed")
        self.assertIn("## Decisions\n", hub)
        self.assertEqual(len(parts), 2)
        self.assertEqual(split.join_parts(hub, parts), ENTITY)

    def test_cli_error_at_exit_zero_stops(self):
        self.write({"Work plan.md": PHASE_PLAN})
        before = snapshot(self.vault)
        before["Work plan/Work plan.md"] = before.pop("Work plan.md")
        code, out = self.run_split("Work plan.md", "--force",
                                   MIMIR_STUB_ERROR="create")
        self.assertNotEqual(code, 0)
        self.assertIn("Error:", out)
        self.assertEqual([c[0] for c in self.calls()],
                         ["version", "vault", "move", "create"])
        self.assertEqual(snapshot(self.vault), before)

    def test_app_not_running_refuses(self):
        self.write({"Work plan.md": PHASE_PLAN})
        before = snapshot(self.vault)
        code, _ = self.run_split("Work plan.md", "--force",
                                 MIMIR_STUB_ERROR="version")
        self.assertNotEqual(code, 0)
        self.assertEqual(self.calls(), [["version"]])
        self.assertEqual(snapshot(self.vault), before)

    def test_other_vault_open_refuses(self):
        self.write({"Work plan.md": PHASE_PLAN})
        before = snapshot(self.vault)
        other = str(self.vault.parent / "Bob vault")
        code, out = self.run_split("Work plan.md", "--force",
                                   MIMIR_STUB_OPEN_VAULT=other)
        self.assertEqual(code, 2, out)
        self.assertIn(other, out)
        self.assertIn(os.path.realpath(self.vault), out)
        self.assertEqual(self.calls(), [["version"], ["vault", "info=path"]])
        self.assertEqual(snapshot(self.vault), before)

    def test_dry_run_writes_nothing(self):
        self.write({"Garden plan.md": TASK_PLAN,
                    "Other.md": "[[Garden plan#Task 1: Beds (build)]]\n"})
        before = snapshot(self.vault)
        code, out = self.run_split("Garden plan", "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertIn("Garden plan – Task 2 Water Paths", out)
        self.assertIn("would move Garden plan.md to Garden plan/Garden plan.md",
                      out)
        self.assertIn("would create Garden plan/Garden plan – Task 1", out)
        self.assertIn("Other.md", out)
        self.assertEqual(snapshot(self.vault), before)
        self.assertEqual(self.calls(), [])

    def test_under_cap_needs_force(self):
        self.write({"Small.md": "## A\n\na\n\n## B\n\nb\n"})
        code, out = self.run_split("Small.md")
        self.assertNotEqual(code, 0)
        self.assertIn("--force", out)
        self.assertEqual(self.calls(), [])
        code, out = self.run_split("Small.md", "--force")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.vault / "Small/Small – B.md").exists())

    def test_skipped_notes_refused(self):
        self.write({"AI/specs/Spec.md": TASK_PLAN,
                    "Board.excalidraw.md": TASK_PLAN,
                    "Seeds – Deck.md": TASK_PLAN,
                    "Recipe.md": "---\ntype: recipe\n---\n" + PHASE_PLAN})
        before = snapshot(self.vault)
        for rel in ("AI/specs/Spec.md", "Board.excalidraw.md",
                    "Seeds – Deck.md", "Recipe.md"):
            with self.subTest(rel=rel):
                code, out = self.run_split(rel, "--force")
                self.assertNotEqual(code, 0)
                self.assertIn("skipped", out)
        self.assertEqual(snapshot(self.vault), before)
        self.assertEqual(self.calls(), [])

    def test_active_plan_refused(self):
        self.write({"Work plan.md": PHASE_PLAN.replace(
            "status: done", "status: active", 1)})
        before = snapshot(self.vault)
        code, out = self.run_split("Work plan.md")
        self.assertNotEqual(code, 0)
        self.assertIn("active", out)
        self.assertEqual(snapshot(self.vault), before)
        self.assertEqual(self.calls(), [])

    def test_template_filled_part_stops_split(self):
        self.write({"Work plan.md": PHASE_PLAN})
        before = (self.vault / "Work plan.md").read_bytes()

        def templater(_seconds):
            for path in self.vault.glob("Work plan/Work plan – *.md"):
                path.write_text("template text\n", encoding="utf-8")

        with mock.patch.object(obsidian_cli.time, "sleep", templater):
            code, out = self.run_split("Work plan.md")
        self.assertNotEqual(code, 0)
        self.assertEqual(
            (self.vault / "Work plan/Work plan.md").read_bytes(), before)

    def test_existing_part_name_refused(self):
        self.write({"Work plan.md": PHASE_PLAN,
                    "Elsewhere/Work plan – Phase 2 Use.md": "taken\n"})
        before = snapshot(self.vault)
        code, out = self.run_split("Work plan.md")
        self.assertNotEqual(code, 0)
        self.assertIn("Work plan – Phase 2 Use", out)
        self.assertEqual(snapshot(self.vault), before)


if __name__ == "__main__":
    unittest.main()
