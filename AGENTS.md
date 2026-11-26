# Agent rules for mimir

These rules apply to every AI agent that works in this repository.

## What this repository is

A Claude Code plugin with one skill, `mimir`, that keeps an Obsidian vault
in shape from one config: it checks the vault's structure and properties,
counts plan progress, splits long notes into a hub and parts, and starts new
notes in the right place. It holds no personal value: a vault keeps its
rules in `.mimir.json` at the vault root, and `config.example.json` shows
the shape.

| Path | Holds |
| --- | --- |
| `.claude-plugin/` | the plugin manifest and the marketplace of one |
| `config.example.json` | an example `.mimir.json` for a made-up vault |
| `skills/mimir/SKILL.md` | the skill: where a note goes, the commands, the red lines |
| `skills/mimir/scripts/` | the config reader, the note reader, the commands |
| `skills/mimir/tests/` | the tests, stdlib `unittest` |
| `evals/` | cases for `claude plugin eval`, with Alice's vault |

## Rules

1. **Nothing personal enters this repository.** No name, mail address,
   hostname, IP address, path of a real home or vault, or secret, in a file
   or in a commit message. A personal value belongs in the vault's
   `.mimir.json`, and the tests and examples use made-up ones (`Alice`,
   `/home/alice/Notes`). `test_privacy.py` checks the tree.
2. **`check` only reads.** Only `check --fix` writes: the counted
   `progress` line, then the frontmatter style (flow lists, key order).
3. **Notes move through the Obsidian CLI.** A command that moves, renames
   or creates a note calls `obsidian` with `path=`, reads stdin from
   `/dev/null`, and treats `Error:` on stdout as a failure even at exit 0.
4. **Text moves, it is never rewritten.** A split keeps every byte of the
   note, and joining the parts gives the original back.
5. **Tests first.** A change to a script comes with its test, and the suite
   is green before a commit:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/mimir/tests
   ```

6. **Python is stdlib only**, run as `python3 -B`, and writes no
   `__pycache__`. The frontmatter reader is a small YAML subset, no PyYAML.
7. **Prose has no em dash** and uses American spelling. CI checks the em
   dash.
8. **Commits follow Conventional Commits 1.0.0.**
