# Changelog

All notable changes to this plugin are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the versions
follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - Unreleased

### Added

- `scripts/config.py`, which reads and validates a vault's `.mimir.json`
  and names the key on any error.
- `scripts/notes.py`, which loads a vault's notes, skipping dot-directories,
  reads their frontmatter (flow and block lists alike) and extracts wikilink
  targets from property values.
- `scripts/check.py`, which checks a vault against its config: states,
  unknown and required keys, `progress` against the counted checkboxes,
  `project` links, folders without their hub, the root, the properties a
  `.base` reads, the size cap and the kinds' templates. `--fix` writes the
  counted `progress` and nothing else.
- `scripts/split.py`, which splits a long note into a hub and parts, moving
  its text byte for byte and rewriting the links to its sections. It
  refuses an active plan.
- `scripts/new.py`, which starts a note of a configured kind from its
  template in its folder, and `scripts/scaffold.py`, which creates the
  missing templates.
- `scripts/obsidian_cli.py`, which calls the Obsidian CLI and treats
  `Error:` on stdout as a failure at exit 0.
- The `mimir` skill: where a note goes, the four commands, and the red
  lines for moving and splitting.
- `README.md`, a complete `config.example.json`, and evals for filing a
  note, starting a project, splitting a plan and fixing `progress`.
