import contextlib
import io
import json
import unittest
from unittest import mock

from helpers import VaultTestCase, config_data, make_config

import check


def plan(status="active", progress=None, body="", extra=""):
    """A plan note's text; progress None leaves the line out."""
    lines = ["---", "type: plan", f"status: {status}", "description: A plan"]
    if progress is not None:
        lines.append(f"progress: {progress}")
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n---\n# Plan\n\n" + body


KEY_ORDER = ["type", "status", "project", "domain", "progress", "updated",
             "created", "description", "tags", "aliases"]


def style_data(**extra) -> dict:
    """config_data() with the style keys and a spec kind that needs a front."""
    data = config_data()
    data["key_order"] = list(KEY_ORDER)
    data["list_style"] = "block"
    data["allowed_keys"] = data["allowed_keys"] + ["related"]
    data["kinds"]["spec"] = {
        "has_status": True, "required": ["description"], "hub": False,
        "progress": None, "template": None, "folder": "specs",
        "front_required": True}
    data.update(extra)
    return data


FLOW_FRONT = (
    "---\n"
    "tags: [garden, \"bee, keeping\", 'it''s']\n"
    "type: guide\n"
    "related: [\"[[Alice]]\", '[[Bob]]']\n"
    "description: >-\n"
    "  Alice keeps bees\n"
    "\n"
    "  in the garden.\n"
    "machine: laptop  # a comment\n"
    "status: active\n"
    "aliases: [Al, ]\n"
    "---\n")
BLOCK_FRONT = (
    "---\n"
    "type: guide\n"
    "status: active\n"
    "description: >-\n"
    "  Alice keeps bees\n"
    "\n"
    "  in the garden.\n"
    "tags:\n"
    "  - garden\n"
    "  - \"bee, keeping\"\n"
    "  - 'it''s'\n"
    "aliases:\n"
    "  - Al\n"
    "related:\n"
    "  - \"[[Alice]]\"\n"
    "  - \"[[Bob]]\"\n"
    "machine: laptop  # a comment\n"
    "---\n")
BODY = ("# Garden\r\ntags: [x]\n\nstatus: done\r\n---\n"
        "type: guide\n  no newline at the end")


class CheckTest(VaultTestCase):
    def run_check(self, data=None):
        return check.check_vault(self.vault, make_config(data))

    def assert_only(self, findings, level, rel, *fragments):
        self.assertEqual(len(findings), 1, findings)
        finding = findings[0]
        self.assertEqual((finding.level, finding.rel), (level, rel))
        for fragment in fragments:
            self.assertIn(fragment, finding.message)

    def run_cli(self, *args, data=None):
        self.write({".mimir.json": json.dumps(
            config_data() if data is None else data)})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(["--vault", str(self.vault), *args])
        return code, out.getvalue()

    # status

    def test_status_outside_list(self):
        self.write({"AI/plans/P.md": plan(status="in-progress")})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/P.md",
                         "in-progress")

    def test_type_exceptions(self):
        self.write({"Computer/Laptop.md": "---\ntype: computer\n"
                                          "status: stale\n---\n"})
        self.assertEqual(self.run_check(), [])
        self.write({"AI/plans/P.md": plan(status="stale")})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/P.md", "stale")

    # frontmatter YAML would reject

    def test_frontmatter_yaml_rejects(self):
        self.write({"Personal/Personal.md": (
            "---\ntype: guide\n"
            "description: Persönliches: Alices Garten und die Bienenstöcke.\n"
            "---\n")})
        findings = self.run_check()
        self.assertIn(check.Finding(
            "ERROR", "Personal/Personal.md",
            'frontmatter line 3: ": " inside an unquoted value, quote it'),
            findings)

    def test_quoted_frontmatter_passes(self):
        self.write({"Workshop/G.md": (
            "---\ntype: guide\n"
            "description: \"Persönliches: Alices Garten\"\n---\n")})
        self.assertEqual(self.run_check(), [])

    # bases

    def test_base_unknown_property(self):
        self.write({"Plans.base": (
            "filters:\n  and:\n    - note.type == \"plan\"\n"
            "    - note.beschreibung != \"\"\n"
            "views:\n  - type: table\n    name: Plans\n    order:\n"
            "      - file.name\n      - status\n      - note.progress\n"
            "      - formula.left\n"),
            "AI/Other.base": "views:\n  - type: table\n    order: "
                             "[file.name, fortschritt]\n"})
        findings = self.run_check()
        got = sorted((f.level, f.rel, f.message) for f in findings)
        self.assertEqual(len(got), 2, got)
        self.assertEqual([g[:2] for g in got],
                         [("ERROR", "AI/Other.base"), ("ERROR", "Plans.base")])
        self.assertIn("fortschritt", got[0][2])
        self.assertIn("beschreibung", got[1][2])

    # keys

    def test_unknown_key_on_typed_note(self):
        self.write({"Workshop/G.md": "---\ntype: guide\nfortschritt: 3\n---\n"})
        self.assert_only(self.run_check(), "ERROR", "Workshop/G.md",
                         "fortschritt")

    def test_grandfathered_key_passes(self):
        self.write({"Workshop/G.md": "---\ntype: guide\nmachine: nas\n"
                                    "tags: [a]\n---\n",
                    "Workshop/Free.md": "---\nanything: 1\n---\n"})
        self.assertEqual(self.run_check(), [])

    def test_required_missing(self):
        self.write({"AI/plans/P.md": "---\ntype: plan\nstatus: active\n---\n"})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/P.md",
                         "description")

    # progress

    def test_progress_mismatch(self):
        body = "- [x] a\n- [ ] b\n- [x] c\n- [ ] d\n"
        self.write({"AI/plans/P.md": plan(progress=0, body=body)})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/P.md",
                         "progress 0, counted 50")

    def test_dash_counts_done(self):
        body = "- [x] a\n- [-] b\n- [ ] c\n- [/] d\n"
        self.write({"AI/plans/P.md": plan(progress=50, body=body)})
        self.assertEqual(self.run_check(), [])

    def test_count_progress_rounds(self):
        self.assertEqual(check.count_progress(["- [x] a\n- [ ] b\n- [ ] c\n"]),
                         33)
        self.assertEqual(check.count_progress(["- [x] a\n", "1. [X] b\n",
                                               "  * [ ] c\n"]), 67)
        self.assertIsNone(check.count_progress(["no boxes\n", ""]))

    def test_fenced_boxes_ignored(self):
        body = ("- [x] a\n```python\n- [ ] not a box\n"
                "# /tmp/probe.py\n```\n~~~~\n- [ ] no\n```\n- [ ] no\n~~~~\n")
        self.write({"AI/plans/P.md": plan(progress=100, body=body)})
        self.assertEqual(self.run_check(), [])

    def split_plan(self, hub_status="active", part_status="active",
                   progress=50):
        self.write({
            "AI/plans/Big.md": plan(
                status=hub_status, progress=progress,
                body="- [x] a\n\n## Parts\n\n- [[Big – Eins]]\n"
                     "- [[Big – Zwei|two]]\n"),
            "AI/plans/Big – Eins.md": "---\ntype: plan\nstatus: "
                                      f"{part_status}\n---\n- [ ] b\n",
            "AI/plans/Big – Zwei.md": "- [x] c\n- [ ] d\n",
        })

    def test_split_hub_counts_parts(self):
        self.split_plan()
        self.assertEqual(self.run_check(), [])
        self.split_plan(progress=100)
        self.assert_only(self.run_check(), "ERROR", "AI/plans/Big.md",
                         "progress 100, counted 50")

    def test_part_carries_no_progress(self):
        self.split_plan()
        self.write({"AI/plans/Big – Zwei.md":
                    "---\nprogress: 50\n---\n- [x] c\n- [ ] d\n"})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/Big – Zwei.md",
                         "progress")

    def test_done_with_open_part(self):
        self.split_plan(hub_status="done", part_status="active", progress=100)
        self.assert_only(self.run_check(), "ERROR", "AI/plans/Big.md",
                         "Big – Eins", "active")
        self.split_plan(hub_status="done", part_status="cancelled",
                        progress=100)
        self.assertEqual(self.run_check(), [])

    # project links

    def write_projects(self, value):
        self.write({
            "Workshop/Pantry/Pantry.md":
                "---\ntype: project\ndescription: Scans\n---\n",
            "Workshop/Guide.md": "---\ntype: guide\n---\n",
            "Workshop/Note.md": f"---\ntype: guide\nproject: {value}\n---\n",
        })

    def test_project_link_unresolved(self):
        self.write_projects('"[[Pantry]]"')
        self.assertEqual(self.run_check(), [])
        self.write_projects('"[[Nope]]"')
        self.assert_only(self.run_check(), "ERROR", "Workshop/Note.md",
                         "Nope")

    def test_project_link_to_non_project(self):
        self.write_projects('\n  - "[[Pantry]]"\n  - "[[Guide]]"')
        self.assert_only(self.run_check(), "ERROR", "Workshop/Note.md",
                         "Guide")

    # folders and the root

    def test_folder_without_hub(self):
        self.write({"Workshop/X/a.md": "text\n",
                    "Workshop/Y/Y.md": "hub\n", "Workshop/Y/b.md": "text\n",
                    "Workshop/Empty/pic.png": b"\x89PNG",
                    "AI/plans/p.md": "text\n"})
        self.assert_only(self.run_check(), "ERROR", "Workshop/X", "X.md")

    def test_root_note_not_allowed(self):
        self.write({"Loose.md": "text\n", "Home.md": "home\n",
                    "Plans.base": "views: []\n"})
        self.assert_only(self.run_check(), "ERROR", "Loose.md")

    def test_root_folder_not_an_area(self):
        self.write({"Stuff/a.md": "text\n", "attachments/a.png": b"x",
                    ".obsidian/app.json": "{}"})
        self.assert_only(self.run_check(), "ERROR", "Stuff")

    # size

    def test_size_cap_warn(self):
        def sized(head):
            data = head.encode("utf-8")
            return data + b"a" * (40961 - len(data))
        self.write({"Workshop/Big.md": sized("---\ntype: guide\n---\n")})
        self.assert_only(self.run_check(), "WARN", "Workshop/Big.md", "40961")
        self.write({"Workshop/Big.md": sized("---\ntype: guide\n---\n")[:-1],
                    "AI/specs/Big.md": sized("---\ntype: guide\n---\n"),
                    "AI/specs/specs.md": "the folder's hub\n",
                    "Workshop/T.md": sized("---\ntype: transaktion\n---\n"),
                    "Workshop/D.excalidraw.md": sized("# Drawing\n")})
        self.assertEqual(self.run_check(), [])

    # templates

    def test_kind_template_missing(self):
        data = config_data()
        data["kinds"]["project"]["template"] = "templates/Project.md"
        self.assert_only(self.run_check(data), "WARN", "templates/Project.md",
                         "missing")
        self.write({"templates/Project.md": "---\ntype: project\n"
                                            "description:\n---\n"})
        self.assert_only(self.run_check(data), "WARN", "templates/Project.md",
                         "status")
        self.write({"templates/Project.md": "---\ntype: project\n"
                                            "status: planned\n"
                                            "description:\n---\n"})
        self.assertEqual(self.run_check(data), [])

    def test_project_without_status_valid(self):
        self.write({"Workshop/Pantry/Pantry.md":
                    "---\ntype: project\ndescription: Scans\n---\n# P\n"})
        self.assertEqual(self.run_check(), [])

    def test_names_survive_in_messages(self):
        self.write({"Workshop/Pantry – Overview für grön.md":
                    "---\ntype: guide\nfortschritt: 1\n---\n"})
        code, out = self.run_cli("--report-only")
        self.assertEqual(code, 0)
        self.assertEqual(out, "ERROR Workshop/Pantry – Overview für grön.md:"
                              " unknown key fortschritt\n")

    # the CLI and --fix

    # formats

    def test_format_rules_off_without_keys(self):
        self.write({"AI/plans/My plan.md": plan(
            extra="updated: 26.11.2026\ntags: [Bienen]").replace(
                "description: A plan", "description: Der Garten und mehr")})
        self.assertEqual(self.run_check(), [])

    def test_date_format(self):
        data = config_data()
        data["date_keys"] = ["updated", "created"]
        self.write({"AI/plans/P.md": plan(extra="updated: 2026-11-26")})
        self.assertEqual(self.run_check(data), [])
        for bad in ("26.11.2026", '"2026-11-26"', "2026-02-30",
                    "2026-11-26 14:31", "[2026-11-26]"):
            self.write({"AI/plans/P.md": plan(extra=f"created: {bad}")})
            self.assert_only(self.run_check(data), "ERROR", "AI/plans/P.md",
                             "created", bad)

    def test_date_empty_passes(self):
        data = config_data()
        data["date_keys"] = ["updated"]
        self.write({"AI/plans/P.md": plan(extra="updated:")})
        self.assertEqual(self.run_check(data), [])

    def test_name_pattern(self):
        data = config_data()
        data["kinds"]["plan"]["name_pattern"] = r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+"
        self.write({"AI/plans/2026-11-26-garden.md": plan()})
        self.assertEqual(self.run_check(data), [])
        self.write({"AI/plans/Garden plan.md": plan()})
        self.assert_only(self.run_check(data), "ERROR",
                         "AI/plans/Garden plan.md", "kind plan",
                         data["kinds"]["plan"]["name_pattern"])

    def test_tag_pattern(self):
        data = config_data()
        data["tag_pattern"] = "[a-z0-9]+(-[a-z0-9]+)*"
        self.write({"AI/plans/P.md": plan(extra="tags: [bee-keeping, garden]")})
        self.assertEqual(self.run_check(data), [])
        self.write({"AI/plans/P.md": plan(
            extra="tags:\n  - garden\n  - Bienen\n  - bee_keeping")})
        self.assert_only(self.run_check(data), "ERROR", "AI/plans/P.md",
                         "Bienen", "bee_keeping")
        self.write({"AI/plans/P.md": plan(extra="tags: Garten-Plan")})
        self.assert_only(self.run_check(data), "ERROR", "AI/plans/P.md",
                         "Garten-Plan")

    def test_warn_words_in_description(self):
        data = config_data()
        data["warn_words"] = ["und", "für"]
        self.write({"Workshop/Garden.md": "---\ntype: guide\n"
                    "description: Alice's beds UND Für the bees\n---\n"})
        self.assert_only(self.run_check(data), "WARN", "Workshop/Garden.md",
                         "und", "für")
        self.write({"Workshop/Garden.md": "---\ntype: guide\n"
                    "description: Undine's beds, fürther along\n---\n"
                    "Alice und Bob\n"})
        self.assertEqual(self.run_check(data), [])

    def test_labels_one_per_property(self):
        self.write({
            "Garden.base": "properties:\n  note.updated:\n"
                           "    displayName: Updated\n",
            "Work.base": "filters:\n  and: []\nproperties:\n  updated:\n"
                         "    displayName: \"Last change\"\n"
                         "  note.status:\n    displayName: Status\n",
            "Seeds.base": "properties:\n  note.updated:\n"
                          "    displayName: Updated\n"
                          "  note.status:\n    displayName: Status\n"})
        self.assert_only(self.run_check(), "ERROR", "Garden.base",
                         "updated", '"Updated" in Garden.base, Seeds.base',
                         '"Last change" in Work.base')

    def test_file_name_labels_may_differ(self):
        self.write({
            "Garden.base": "properties:\n  file.name:\n"
                           "    displayName: Plan\n",
            "Work.base": "properties:\n  file.name:\n"
                         "    displayName: Session\n"})
        self.assertEqual(self.run_check(), [])

    def test_labels_inline_base_block(self):
        self.write({
            "Garden.base": "properties:\n  note.updated:\n"
                           "    displayName: Updated\n",
            "Workshop/Beds.md": "# Beds\n\n```base\nproperties:\n"
                               "  note.updated:\n    displayName: Changed\n"
                               "```\n\n```yaml\nproperties:\n"
                               "  note.updated:\n    displayName: Other\n"
                               "```\n"})
        self.assert_only(self.run_check(), "ERROR", "Garden.base",
                         '"Changed" in Workshop/Beds.md',
                         '"Updated" in Garden.base')
        self.assertNotIn("Other", self.run_check()[0].message)

    def test_fix_writes_progress(self):
        crlf = plan(progress=0, body="- [x] a\n- [ ] b\n").replace("\n", "\r\n")
        lf = plan(body="- [x] a\n- [x] b\n- [ ] c\n- [ ] d\n",
                  extra="tags: [x]")
        self.write({"AI/plans/A.md": crlf, "AI/plans/B.md": lf})
        code, out = self.run_cli()
        self.assertEqual(code, 1)
        self.assertIn("ERROR AI/plans/B.md: progress missing, counted 50", out)
        code, out = self.run_cli("--fix")
        self.assertEqual((code, out), (0, ""))
        self.assertEqual((self.vault / "AI/plans/A.md").read_bytes(),
                         crlf.replace("progress: 0\r\n", "progress: 50\r\n")
                         .encode("utf-8"))
        self.assertEqual((self.vault / "AI/plans/B.md").read_bytes(),
                         lf.replace("status: active\n",
                                    "status: active\nprogress: 50\n")
                         .encode("utf-8"))
        self.assertEqual(check.fix_progress(self.vault, make_config()), [])
        self.assertEqual(self.run_cli(), (0, ""))


    # frontmatter style

    def run_cli_err(self, *args, data=None):
        self.write({".mimir.json": json.dumps(
            style_data() if data is None else data)})
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = check.main(["--vault", str(self.vault), *args])
        return code, out.getvalue(), err.getvalue()

    def test_list_style_flow_where_block(self):
        self.write({"Workshop/G.md": "---\ntype: guide\ntags: [a, b]\n"
                                    "aliases: [Al]\n---\n"})
        self.assert_only(self.run_check(style_data()), "ERROR",
                         "Workshop/G.md", "list style", "tags", "aliases")
        self.write({"Workshop/G.md": "---\ntype: guide\ntags:\n  - a\n"
                                    "  - b\naliases: []\n---\n"})
        self.assertEqual(self.run_check(style_data()), [])

    def test_list_style_block_where_flow(self):
        data = style_data(list_style="flow")
        self.write({"Workshop/G.md": "---\ntype: guide\ntags:\n  - a\n---\n"})
        self.assert_only(self.run_check(data), "ERROR", "Workshop/G.md",
                         "list style", "tags")
        self.write({"Workshop/G.md": "---\ntype: guide\ntags: [a]\n---\n"})
        self.assertEqual(self.run_check(data), [])

    def test_key_order(self):
        self.write({"Workshop/G.md": "---\nstatus: active\ntype: guide\n"
                                    "---\n"})
        self.assert_only(self.run_check(style_data()), "ERROR",
                         "Workshop/G.md", "key order", "type")
        self.write({"Workshop/G.md": "---\nmachine: laptop\ntype: guide\n"
                                    "---\n"})
        self.assert_only(self.run_check(style_data()), "ERROR",
                         "Workshop/G.md", "key order", "type")
        self.write({"Workshop/G.md": "---\ntype: guide\nstatus: active\n"
                                    "description: A guide\nrelated: x\n"
                                    "machine: laptop\n---\n"})
        self.assertEqual(self.run_check(style_data()), [])

    def test_no_frontmatter(self):
        self.write({"AI/specs/S.md": "# S\n\nA spec without a front.\n",
                    "AI/plans/P-summary.md": "# P\n"})
        self.assert_only(self.run_check(style_data()), "ERROR",
                         "AI/specs/S.md", "no frontmatter", "spec")
        self.write({"AI/specs/S.md": "---\ntype: spec\nstatus: active\n"
                                     "description: A spec\n---\n# S\n"})
        self.assertEqual(self.run_check(style_data()), [])

    def test_style_rules_off_without_keys(self):
        self.write({"Workshop/G.md": "---\nstatus: active\ntype: guide\n"
                                    "tags: [a]\n---\n",
                    "AI/specs/S.md": "# S\n"})
        data = style_data()
        del data["key_order"], data["list_style"]
        data["kinds"]["spec"]["front_required"] = False
        self.assertEqual(self.run_check(data), [])
        self.assertEqual(check.fix_style(self.vault, make_config(data)),
                         ([], []))

    def test_style_skip_names(self):
        drawing = "---\nexcalidraw-plugin: parsed\ntags: [excalidraw]\n---\n"
        self.write({"Workshop/D.excalidraw.md": drawing})
        self.assertEqual(self.run_check(style_data()), [])
        self.assertEqual(check.fix_style(self.vault, make_config(style_data())),
                         ([], []))
        self.assertEqual((self.vault / "Workshop/D.excalidraw.md").read_text(),
                         drawing)

    def test_fix_style_round_trip(self):
        crlf = (FLOW_FRONT.replace("\n", "\r\n") + BODY).encode("utf-8")
        self.write({"Workshop/G.md": FLOW_FRONT + BODY, "Workshop/C.md": crlf})
        findings = self.run_check(style_data())
        messages = [f.message for f in findings if f.rel == "Workshop/G.md"]
        self.assertEqual(len(messages), 2, findings)
        self.assertTrue(any(m.startswith("list style") for m in messages))
        self.assertTrue(any(m.startswith("key order") for m in messages))
        code, out, err = self.run_cli_err("--fix")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("fixed Workshop/G.md: style", err)
        self.assertIn("fixed Workshop/C.md: style", err)
        self.assertEqual((self.vault / "Workshop/G.md").read_bytes(),
                         (BLOCK_FRONT + BODY).encode("utf-8"))
        self.assertEqual((self.vault / "Workshop/C.md").read_bytes(),
                         (BLOCK_FRONT.replace("\n", "\r\n") + BODY)
                         .encode("utf-8"))
        cfg = make_config(style_data())
        self.assertEqual(check.fix_style(self.vault, cfg), ([], []))
        self.assertEqual(check.check_vault(self.vault, cfg), [])

    def test_fix_progress_then_style(self):
        text = "---\nstatus: active\ntype: plan\ndescription: A plan\n---\n" \
               "- [x] a\n- [ ] b\n"
        self.write({"AI/plans/P.md": text})
        code, out, err = self.run_cli_err("--fix")
        self.assertEqual((code, out), (0, ""))
        self.assertIn("fixed AI/plans/P.md: progress", err)
        self.assertIn("fixed AI/plans/P.md: style", err)
        self.assertEqual((self.vault / "AI/plans/P.md").read_text(),
                         "---\ntype: plan\nstatus: active\nprogress: 50\n"
                         "description: A plan\n---\n- [x] a\n- [ ] b\n")

    def test_unreadable_front_not_rewritten(self):
        files = {
            "Workshop/N.md": "---\nstatus: active\ntype: guide\n"
                            "tags: [a, [b, c]]\n---\nbody\n",
            "Workshop/M.md": "---\nstatus: active\ntype: guide\n"
                            "tags: [a,\n  b]\n---\nbody\n",
            "Workshop/A.md": "---\nstatus: active\ntype: guide\n"
                            "aliases: &names\n  - Al\n---\nbody\n",
            "Workshop/B.md": "---\nstatus: active\n# a comment\n"
                            "type: guide\n---\nbody\n",
        }
        self.write(files)
        code, out, err = self.run_cli_err("--fix")
        self.assertEqual(code, 1)
        for rel, text in files.items():
            self.assertEqual((self.vault / rel).read_text(), text)
            self.assertIn(f"{rel}: style: not rewritten", err)
            self.assertIn(f"ERROR {rel}: key order", out)
        self.assertNotIn("fixed", err)
        self.assertIn("nested flow list", err)
        self.assertIn("multi-line flow list", err)
        self.assertIn("anchor", err)

    def test_keep_chomped_scalar_not_moved(self):
        cfg = make_config(style_data())
        keep = "---\ntags:\n  - a\ndescription: |+\n  one\n\n\n---\n# Body\n"
        new, reason = check.restyle(keep, cfg)
        self.assertIsNone(new)
        self.assertTrue(reason)
        inner = "---\ndescription: |+\n  one\ntype: guide\n---\n"
        self.assertEqual(check.restyle(inner, cfg),
                         (None, "a keep-chomped block scalar that would move"))
        tail = "---\ntags:\n  - a\ntype: guide\n\n---\n"
        self.assertEqual(check.restyle(tail, cfg),
                         (None, "a blank line after the last key, which "
                                "would move"))
        stays = "---\ntype: guide\ndescription: |+\n  one\n\n\ntags: [a]\n\n---\n"
        self.assertEqual(check.restyle(stays, cfg),
                         (stays.replace("tags: [a]\n", "tags:\n  - a\n"),
                          None))

    def test_final_comparison_sees_kept_blank_lines(self):
        keep = "---\ntags:\n  - a\ndescription: |+\n  one\n\n\n---\n# Body\n"
        lost = "---\ndescription: |+\n  one\ntags:\n  - a\n\n\n---\n# Body\n"
        self.assertTrue(check._same_front(keep, keep))
        self.assertFalse(check._same_front(keep, lost))

    def test_style_refusals(self):
        cfg = make_config(style_data())
        cases = {
            "---\ntags: [a]\ntags: [b]\n---\n": "a key that appears twice",
            "---\ntags: [a]\nnot a key\n---\n":
                "a top-level line that is no key",
            "---\ntags: [a]\naliases:\n\t- x\n---\n":
                "a frontmatter a YAML parser would reject",
        }
        for text, reason in cases.items():
            with self.subTest(text=text):
                self.assertEqual(check.restyle(text, cfg), (None, reason))
        with mock.patch.object(check, "_block_list",
                               return_value=["tags:\n", "  - b\n"]):
            self.assertEqual(check.restyle("---\ntags: [a]\n---\n", cfg),
                             (None, "the rewrite would change a value"))

    def test_front_required_spares_templates(self):
        data = style_data()
        data["kinds"]["spec"]["template"] = "AI/specs/templates/Spec.md"
        self.write({"AI/specs/templates/Spec.md": "# Spec\n"})
        findings = self.run_check(data)
        self.assertEqual([f for f in findings if "no frontmatter" in f.message],
                         [], findings)

    def test_flow_list_of_wikilinks_reported(self):
        files = {"Workshop/W.md": "---\ntype: guide\n"
                                 "related: [[[Alice]], [[Bob]]]\n---\nbody\n",
                 "Workshop/L.md": "---\ntype: guide\nrelated: [[Alice]]\n"
                                 "---\nbody\n"}
        self.write(files)
        self.assert_only(self.run_check(style_data()), "ERROR",
                         "Workshop/W.md", "list style", "related")
        code, out, err = self.run_cli_err("--fix")
        for rel, text in files.items():
            self.assertEqual((self.vault / rel).read_text(), text)
        self.assertIn("Workshop/W.md: style: not rewritten, a flow list of "
                      "unquoted wikilinks", err)
        self.assertNotIn("Workshop/L.md", err + out)

    def test_done_plan_expects_100(self):
        self.write({"AI/plans/P.md": plan(status="done", progress=100,
                                          body="- [x] a\n- [ ] b\n")})
        self.assertEqual(self.run_check(), [])
        self.write({"AI/plans/P.md": plan(status="done", progress=50,
                                          body="- [x] a\n- [ ] b\n")})
        self.assert_only(self.run_check(), "ERROR", "AI/plans/P.md",
                         "progress 50", "100")
        self.assertEqual(check.fix_progress(self.vault, make_config()),
                         [self.vault / "AI/plans/P.md"])
        self.assertIn("progress: 100\n",
                      (self.vault / "AI/plans/P.md").read_text())

    def test_cancelled_plan_progress_not_checked(self):
        self.write({"AI/plans/P.md": plan(status="cancelled", progress=10,
                                          body="- [x] a\n- [ ] b\n"),
                    "AI/plans/Q.md": plan(status="cancelled",
                                          body="- [x] a\n")})
        self.assertEqual(self.run_check(), [])
        self.assertEqual(check.fix_progress(self.vault, make_config()), [])

    def test_cli_exit_codes(self):
        self.write({"Loose.md": "text\n"})
        self.assertEqual(self.run_cli(),
                         (1, "ERROR Loose.md: not allowed at the root\n"))
        self.assertEqual(self.run_cli("--report-only")[0], 0)
        data = config_data()
        del data["states"]
        self.assertEqual(self.run_cli(data=data), (2, ""))


    # check safety

    def test_progress_counts_nested_parts(self):
        self.write({
            "AI/plans/Big.md": plan(
                progress=75,
                body="- [x] a\n\n## Parts\n\n- [[Big – Eins]]\n"),
            "AI/plans/Big – Eins.md":
                "- [ ] b\n\n## Parts\n\n- [[Big – Eins – Tief]]\n",
            "AI/plans/Big – Eins – Tief.md": "- [x] c\n- [x] d\n",
        })
        self.assertEqual(self.run_check(), [])
        self.write({
            "AI/plans/Big.md": plan(
                status="done", progress=100,
                body="- [x] a\n\n## Parts\n\n- [[Big – Eins]]\n"),
            "AI/plans/Big – Eins.md":
                "---\nstatus: done\n---\n- [x] b\n\n## Parts\n\n"
                "- [[Big – Eins – Tief]]\n",
            "AI/plans/Big – Eins – Tief.md":
                "---\nstatus: active\n---\n- [ ] c\n",
        })
        hub = [f for f in self.run_check() if f.rel == "AI/plans/Big.md"]
        self.assert_only(hub, "ERROR", "AI/plans/Big.md",
                         "Big – Eins – Tief", "active")

    def test_dangling_symlink_warns(self):
        self.write({"AI/plans/P.md": plan(progress=100, body="- [x] a\n")})
        (self.vault / "AI/plans/x.md").symlink_to(self.vault / "AI/gone.md")
        self.assert_only(self.run_check(), "WARN", "AI/plans/x.md",
                         "symlink", "missing")
        code, out, err = self.run_cli_err("--fix", data=config_data())
        self.assertEqual((code, err), (0, ""))
        self.assertIn("WARN AI/plans/x.md:", out)


if __name__ == "__main__":
    unittest.main()
