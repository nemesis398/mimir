import unittest

from helpers import VaultTestCase

import notes


class ParseFrontTest(unittest.TestCase):
    def test_flow_and_block_lists_parse_alike(self):
        flow = "---\ntype: plan\ntags: [a, b]\n---\nBody\n"
        block = "---\ntype: plan\ntags:\n  - a\n  - b\n---\nBody\n"
        flow_front, flow_body = notes.parse_front(flow)
        block_front, block_body = notes.parse_front(block)
        self.assertEqual(flow_front, {"type": "plan", "tags": ["a", "b"]})
        self.assertEqual(flow_front, block_front)
        self.assertEqual(flow_body, block_body)

    def test_block_list_without_indent_parses(self):
        text = "---\naliases:\n- One\n- \"Two, three\"\nstatus: active\n---\n"
        front, _ = notes.parse_front(text)
        self.assertEqual(front, {"aliases": ["One", "Two, three"],
                                 "status": "active"})

    def test_quoted_link_value(self):
        front, _ = notes.parse_front('---\nproject: "[[Pantry]]"\n---\n')
        self.assertEqual(notes.links(front["project"]), ["Pantry"])
        two = '---\nproject:\n  - "[[Pantry]]"\n  - "[[grön]]"\n---\n'
        front, _ = notes.parse_front(two)
        self.assertEqual(notes.links(front["project"]), ["Pantry", "grön"])
        flow = '---\nproject: ["[[A]]", "[[B]]"]\n---\n'
        front, _ = notes.parse_front(flow)
        self.assertEqual(notes.links(front["project"]), ["A", "B"])

    def test_links_forms(self):
        value = ("[[X]], [[Y|shown]], [[Z#Heading]], [[W#H|shown]], "
                 "[[Folder/Sub/V]], [[#Only heading]]")
        self.assertEqual(notes.links(value), ["X", "Y", "Z", "W", "V"])
        self.assertEqual(notes.links(None), [])
        self.assertEqual(notes.links(42), [])
        self.assertEqual(notes.links(["[[A]]", None, "plain"]), ["A"])

    def test_scalars(self):
        text = ("---\nprogress: 50\nverified: true\nupdated: 2026-01-02\n"
                "description: 'It''s here: yes'\nempty:\n"
                "title: Pantry-Umbau – Overview # a comment\n---\n")
        front, _ = notes.parse_front(text)
        self.assertEqual(front["progress"], 50)
        self.assertIs(front["verified"], True)
        self.assertEqual(front["updated"], "2026-01-02")
        self.assertEqual(front["description"], "It's here: yes")
        self.assertIsNone(front["empty"])
        self.assertEqual(front["title"], "Pantry-Umbau – Overview")

    def test_folded_block_scalar(self):
        text = "---\ndescription: >-\n  One line\n  and more.\ntype: x\n---\n"
        front, _ = notes.parse_front(text)
        self.assertEqual(front["description"], "One line and more.")
        self.assertEqual(front["type"], "x")

    def test_nested_map_passed_through_raw(self):
        text = ("---\nexcalidraw-plugin: parsed\nsettings:\n  a: 1\n"
                "  b: two\ntype: note\n---\n")
        front, _ = notes.parse_front(text)
        self.assertEqual(front["excalidraw-plugin"], "parsed")
        self.assertEqual(front["settings"], "  a: 1\n  b: two")
        self.assertEqual(front["type"], "note")

    def test_no_frontmatter(self):
        text = "# Title\n\n---\nnot: front\n---\n"
        self.assertEqual(notes.parse_front(text), (None, text))
        unclosed = "---\ntype: plan\nno end\n"
        self.assertEqual(notes.parse_front(unclosed), (None, unclosed))

    def test_crlf(self):
        front, body = notes.parse_front("---\r\ntype: plan\r\n---\r\nBody\r\n")
        self.assertEqual(front, {"type": "plan"})
        self.assertEqual(body, "Body\r\n")

    def test_fenced_heading_stays_in_body(self):
        body = ("## Step\n\n```python\n# /tmp/scratch-dir/check_run.py\n"
                "---\nkey: value\n```\n")
        front, got = notes.parse_front("---\ntype: plan\n---\n" + body)
        self.assertEqual(front, {"type": "plan"})
        self.assertEqual(got, body)


class LoadNotesTest(VaultTestCase):
    def test_dot_dirs_skipped(self):
        self.write({
            "Home.md": "# Home\n",
            "a/b.md": "---\ntype: plan\n---\nText\n",
            ".obsidian/x.md": "hidden\n",
            "a/.agents/y.md": "hidden\n",
            "a/not-a-note.txt": "text\n",
        })
        loaded = notes.load_notes(self.vault)
        self.assertEqual([n.rel for n in loaded], ["Home.md", "a/b.md"])

    def test_note_fields(self):
        content = "---\ntype: plan\n---\ngrön – Overview\n"
        self.write({"AI/grön – Overview.md": content})
        (note,) = notes.load_notes(self.vault)
        self.assertEqual(note.rel, "AI/grön – Overview.md")
        self.assertEqual(note.path, self.vault / "AI/grön – Overview.md")
        self.assertEqual(note.front, {"type": "plan"})
        self.assertEqual(note.body, "grön – Overview\n")
        self.assertEqual(note.size, len(content.encode("utf-8")))


HUB_LINE = "description: Persönliches: Alices Garten und die Bienenstöcke."


def front(*lines):
    return "---\n" + "\n".join(lines) + "\n---\nBody\n"


class FrontErrorsTest(unittest.TestCase):
    def assert_clean(self, *lines):
        self.assertEqual(notes.front_errors(front(*lines)), [], lines)

    def assert_error(self, line_no, fragment, *lines):
        errors = notes.front_errors(front(*lines))
        self.assertEqual(len(errors), 1, errors)
        self.assertTrue(errors[0].startswith(f"frontmatter line {line_no}: "),
                        errors)
        self.assertIn(fragment, errors[0])

    def test_hub_line_with_colon_space_rejected(self):
        self.assert_error(3, '": "', "type: area", HUB_LINE)

    def test_hub_line_keeps_its_parse(self):
        front_, _ = notes.parse_front(front(HUB_LINE))
        self.assertEqual(front_["description"],
                         "Persönliches: Alices Garten und die Bienenstöcke.")

    def test_quoted_hub_line_passes(self):
        self.assert_clean('description: "Persönliches: Alices Garten und die '
                          'Bienenstöcke."')
        self.assert_clean("description: 'Persönliches: Alices Garten'")

    def test_values_yaml_accepts_pass(self):
        self.assert_clean("url: https://example.com/a", "time: 12:30",
                          "tags: [a, b]", "aliases:", "  - One",
                          '  - "Two: three"', "related:", "- a: b",
                          "note: a #comment: here", "folded: >-",
                          "  Text with: a colon", "  and more: text",
                          "literal: |", "  key: value", "empty:",
                          "link: [[A]]", "multi: one", "  two",
                          "quoted: \"one", "  two\"", "flow: [a,", "  b]",
                          "nested:", "  inner: value", "colon: :x")

    def test_trailing_colon_rejected(self):
        self.assert_error(2, '":" at the end', "description: Fazit:")

    def test_colon_space_on_continuation_line(self):
        self.assert_error(3, '": "', "description: one", "  two: three")

    def test_colon_space_in_list_item_value(self):
        self.assert_error(3, '": "', "items:", "  - a: b: c")

    def test_unclosed_quote(self):
        self.assert_error(2, "quote", 'description: "open', "type: plan")

    def test_text_after_closing_quote(self):
        self.assert_error(2, "after", 'description: "a" b')

    def test_unclosed_flow_list(self):
        self.assert_error(2, "[", "tags: [a, b", "type: plan")

    def test_text_after_flow_list(self):
        self.assert_error(2, "after", "related: [[A]], [[B]]")

    def test_tab_in_indentation(self):
        self.assert_error(3, "tab", "tags:", "\t- a")

    def test_reserved_first_character(self):
        self.assert_error(2, "@", "owner: @alice")

    def test_list_item_after_scalar(self):
        self.assert_error(3, "list", "type: plan", "- stray")

    def test_one_error_per_problem(self):
        errors = notes.front_errors(front(HUB_LINE, "type: plan",
                                          "other: a: b"))
        self.assertEqual([e.split(":")[0] for e in errors],
                         ["frontmatter line 2", "frontmatter line 4"])

    def test_no_frontmatter_no_errors(self):
        self.assertEqual(notes.front_errors("# Title\nkey: a: b\n"), [])
        self.assertEqual(notes.front_errors(""), [])


if __name__ == "__main__":
    unittest.main()
