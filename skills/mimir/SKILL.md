---
name: mimir
description: >-
  Keep an Obsidian vault in shape from the rules in its `.mimir.json`, which
  name the areas, the states, the kinds of note and their folders. Use this
  when a note has to be filed or moved and the question is where it goes,
  when a project, plan or other configured kind of note is started, when a
  long note or a finished plan is split into a hub and parts, when a plan's
  `progress` has to be counted, and whenever `mimir check` or vault-check
  reports a mimir finding. It holds the decision of where a note goes, the
  four commands with their flags, and the red lines for moving and splitting.
allowed-tools:
  - Bash(python3 -B "${CLAUDE_SKILL_DIR}/scripts/check.py":*)
---

# mimir

mimir keeps one vault's structure in line with one file, `.mimir.json` at
the vault root. Read that file before you decide anything: it is the only
source of the vault's areas, states and kinds, and this skill holds none of
them. When it is missing or a command exits 2 naming a key, stop and tell
the user which key to fix in `.mimir.json`.

The vault is the directory that holds `.mimir.json`: `MIMIR_VAULT` when that
is set, else the current directory or the nearest parent that has the file.
Pass it as `--vault` to every command below, because only `check.py` reads
`MIMIR_VAULT` on its own. The commands are `"${CLAUDE_SKILL_DIR}/scripts/"`
followed by the script name, run with `python3 -B`.

## Where a note goes

Decide in this order, reading each value from `.mimir.json`:

1. **Its kind.** A note's kind is the entry in `kinds` whose `type` it
   carries: `note_type` when the kind sets one, else the kind's name. Two
   kinds may share a `type` (a `season` kind can make `type: project`
   notes); then the kind whose `folder` is in the note's path wins.
2. **Its area.** One of `areas`, the top-level folders. Ask the user when
   the note fits two. Nothing goes in a `system_folders` entry by hand, and
   at the root only what `root_allowlist` names (patterns such as `*.base`).
3. **Its folder.** Inside the area:

   | The kind has | The note is |
   | --- | --- |
   | `hub: true`, no `folder` | `<area>/<name>/<name>.md`, a folder with its hub |
   | `hub: true` and a `folder` | `<area>/<folder>/<name>/<name>.md` |
   | `hub: false` and a `folder` | `<area>/<folder>/<name>.md` |
   | `hub: false`, no `folder` | `<area>/<name>.md` |

   Every folder with notes in it below an area needs its hub, the note of
   the folder's own name, unless a kind's `folder` names it.
4. **Its project.** A note that belongs to a project carries
   `project: "[[Hub]]"`, a quoted wikilink to a `type: project` note, and
   stays where its kind puts it.
5. **Its frontmatter.** Its `status` is one of `states`, or one of the extra
   states `states_by_type` lists for its `type`. A note with a `type` uses
   only the keys in `view_properties`, `allowed_keys` and its kind's
   `required`, and every `required` key has a value.

A note whose `type` no kind names is filed by area alone. Propose the path
and wait for the user before you move a note that already exists.

## The commands

| Command | Does |
| --- | --- |
| `check.py [--vault PATH] [--fix] [--report-only]` | checks the vault and prints one `LEVEL rel: message` line per finding |
| `new.py KIND NAME --area AREA [--parent NAME] [--vault PATH]` | starts a note of a kind from its template and prints its path |
| `split.py NOTE [--vault PATH] [--level N] [--dry-run] [--force]` | splits a note into a hub and parts |
| `scaffold.py [--vault PATH] [--dry-run]` | creates the missing templates of the kinds |

### check

`check.py` only reads. It exits 1 on any ERROR, 0 with `--report-only`, and
2 when `.mimir.json` is wrong. `--fix` first writes the counted `progress`
into each note whose kind has `"progress": "checkboxes"`, changing only that
line, then checks. Progress is the share of `[x]` and `[-]` boxes among all
boxes outside fenced code, as a whole number; a hub counts its parts too,
and a part carries no `progress` of its own.

What a finding asks for:

- `status "..." is not allowed`: set a state from `states` or from
  `states_by_type` for the note's type. Ask the user which one is true.
- `unknown key ...`: the key is in no list of the config. Rename it to a
  known one or ask the user whether the config should allow it.
- `... missing, kind ... requires it`: fill the key from the note's text;
  do not invent a value.
- `progress ..., counted ...` or `progress missing`: run `check.py --fix`.
- `done, but part ... is ...`: the hub is closed while a part is open.
  Ask the user which of the two is wrong.
- `project [[...]] does not resolve` or `is not a type project note`:
  point `project` at the project's hub.
- `folder without its hub ...`: create the hub with `new.py`, or move the
  notes where their kind puts them.
- `not allowed at the root` or `folder at the root is no area`: file the
  note or folder into an area (see "Where a note goes").
- `unknown property ...` in a `.base`: the base reads a property that no
  note may carry. Fix the base, not the notes.
- WARN `... bytes, over the cap`: split the note, unless it is an active
  plan.
- WARN `template of kind ... is missing` or `has no status`: run
  `scaffold.py`, or add `status:` to the template.

### new

`new.py` makes a kind's note where "Where a note goes" puts it, from the
kind's `template` with `{{name}}` and `{{date}}` filled. `--parent NAME`
sets `project: "[[NAME]]"`. It refuses a name that exists anywhere in the
vault, a name with one of `/ \ : # ^ | [ ] * " < > ?`, an unknown kind or
area, and a vault where `obsidian version` fails. It exits 0 and prints the
path, 1 on a refusal, 2 on a config error.

### split

`split.py` cuts a note at one heading depth: the lowest that occurs more
than once outside fenced code, or `--level N`. For a plan with
`## Phase` headings, split at the phase level. The hub keeps the note's
path, its frontmatter and the text above the first section, then a
`## Parts` list. Each part is one section, named `<note> – <heading>`,
with the en dash kept. Links to `[[Note#Heading]]` are rewritten to the
part; plain links keep resolving to the hub.

Always run `--dry-run` first and show the user its output. `split.py`
refuses a note under the size cap unless `--force` is given, a note the
config skips (`size_skip_folders`, `size_skip_types`, `size_skip_names`), a
plan with `status: active`, a part name that exists anywhere, and a vault
where the Obsidian app does not answer. After a split, give the parts their
frontmatter (a plan's parts get `type: plan-part` and the hub's `status`,
no `progress`) and run `check.py --fix`.

### scaffold

`scaffold.py` writes a template for each kind whose `template` file is
missing: frontmatter with the kind's `type`, `status: planned` when the kind
has `has_status`, and its `required` keys empty. It never touches a file
that exists. Run `--dry-run` first.

## Red lines

- **Notes move only through the Obsidian CLI**, with the app running:
  `obsidian move path=<old> to=<new>`, `obsidian rename path=...`,
  `obsidian delete path=...`, each with `< /dev/null`. Never `mv`, `rm` or a
  rename in the shell, because only Obsidian rewrites the links. An `Error:`
  on stdout is a failure even at exit 0: stop there.
- **Never split an active plan.** A plan over the cap is split right after
  it is written or once it is closed, never while it runs.
- **Text moves, it is never rewritten.** A split or a move keeps every
  sentence. Joining the parts gives the original note back; do not reword,
  shorten or merge text on the way.
- **`check` reads, `--fix` writes only `progress`.** Every other finding is
  fixed by hand, one batch at a time, after the user agrees.
