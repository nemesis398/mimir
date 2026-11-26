import contextlib
import io
import json
import unittest

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
        self.split_plan(hub_status="done", part_status="active")
        self.assert_only(self.run_check(), "ERROR", "AI/plans/Big.md",
                         "Big – Eins", "active")
        self.split_plan(hub_status="done", part_status="cancelled")
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

    def test_cli_exit_codes(self):
        self.write({"Loose.md": "text\n"})
        self.assertEqual(self.run_cli(),
                         (1, "ERROR Loose.md: not allowed at the root\n"))
        self.assertEqual(self.run_cli("--report-only")[0], 0)
        data = config_data()
        del data["states"]
        self.assertEqual(self.run_cli(data=data), (2, ""))


if __name__ == "__main__":
    unittest.main()
