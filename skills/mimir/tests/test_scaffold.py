import contextlib
import io
import json
import unittest

from helpers import VaultTestCase, config_data, make_config

import scaffold


def with_templates() -> dict:
    data = config_data()
    data["kinds"]["project"]["template"] = "templates/Project.md"
    data["kinds"]["plan"]["template"] = "templates/Plan.md"
    return data


class ScaffoldTest(VaultTestCase):
    def test_creates_missing_templates(self):
        made = scaffold.scaffold(self.vault, make_config(with_templates()))
        self.assertEqual({p.name for p in made}, {"Project.md", "Plan.md"})
        text = (self.vault / "templates/Plan.md").read_text(encoding="utf-8")
        self.assertIn("type: plan\nstatus: planned\ndescription:\n", text)

    def test_scaffold_never_overwrites(self):
        self.write({"templates/Plan.md": "Alice's own template\n"})
        made = scaffold.scaffold(self.vault, make_config(with_templates()))
        self.assertEqual([p.name for p in made], ["Project.md"])
        self.assertEqual((self.vault / "templates/Plan.md").read_bytes(),
                         b"Alice's own template\n")

    def test_dry_run_writes_nothing(self):
        self.write({".mimir.json": json.dumps(with_templates())})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = scaffold.main(["--vault", str(self.vault), "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("would create templates/Plan.md", out.getvalue())
        self.assertFalse((self.vault / "templates").exists())


if __name__ == "__main__":
    unittest.main()
