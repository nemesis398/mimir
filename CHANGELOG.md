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
- `notes.front_errors`, and with it a `check` ERROR naming the line, for
  frontmatter a YAML parser would reject: `: ` or a final `:` inside an
  unquoted value, an unclosed quote or flow list, text after the closing
  quote or bracket, a tab in the indentation, a reserved first character
  and a stray list item. Obsidian marks such properties as invalid.
- `scripts/split.py`, which splits a long note into a hub and parts, moving
  its text byte for byte and rewriting the links to its sections. Every
  split note lives in its own folder, `<dir>/<name>/<name>.md`, with its
  parts next to it; a note elsewhere is moved there through the app first.
  The hub keeps the leading sections named with `--keep`, for a plan those
  before the first `Phase` or `Task` heading. It refuses an active plan and
  keeps CRLF line endings in the files whose links it rewrites.
- `scripts/new.py`, which starts a note of a configured kind from its
  template in its folder, keeping the template's line endings, and `scripts/scaffold.py`, which creates the
  missing templates.
- `scripts/obsidian_cli.py`, which calls the Obsidian CLI in the vault's
  directory and treats `Error:` on stdout as a failure at exit 0.
- The `mimir` skill: where a note goes, the four commands, and the red
  lines for moving and splitting.
- `README.md`, a complete `config.example.json`, and evals for filing a
  note, starting a project, splitting a plan and fixing `progress`.
