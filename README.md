# mimir

A [Claude Code](https://claude.com/claude-code) plugin that keeps an
[Obsidian](https://obsidian.md) vault in shape from one config file. It
checks the vault's folders and frontmatter, counts the progress of plans,
splits long notes into a hub and parts, and starts new notes in the place
their kind belongs.

A vault drifts one note at a time: a status nobody else uses, a property
spelled two ways, a plan whose `progress` stopped matching its checkboxes,
a note too long to read at the root. mimir writes the vault's rules down
once, in `.mimir.json` at the vault root, and checks every note against
them. The skill reads the same file to decide where a note goes, so the
agent and the checker never disagree.

## What it does

- **The skill** decides where a note goes from the config, says which
  command fixes which finding, and holds the red lines: notes move only
  through the Obsidian CLI, an active plan is never split, and text moves
  without being rewritten.
- **`check.py`** checks the vault: states, unknown keys, required keys,
  `progress` against the counted checkboxes, `project` links, folders
  without their hub, the root, properties that a `.base` reads, the size
  cap and the kinds' templates. `--fix` writes the counted `progress`.
- **`new.py`** starts a note of a kind from its template, in its folder.
- **`split.py`** splits a note into a hub and parts in the note's own
  folder, moving its text byte for byte, and rewrites the links to its
  sections.
- **`scaffold.py`** creates the templates that the kinds name and the
  vault lacks.

## Install

In Claude Code, add the galdr marketplace and install the plugin from it:

```text
/plugin marketplace add nemesis398/galdr
/plugin install mimir@galdr
```

The repository is a marketplace of one as well:

```text
/plugin marketplace add nemesis398/mimir
/plugin install mimir@mimir
```

It needs Python 3.10 or later as `python3` on the `PATH`, and nothing
outside the standard library. `new.py` and `split.py` also need the
Obsidian command line, `obsidian`, with the app running; `MIMIR_OBSIDIAN`
names another binary.

## Configure

The rules live in `.mimir.json` at the vault root. `config.example.json`
shows one for a made-up vault with two areas. Every top-level key is
required except the three format keys at the end of the table, whose rule is
off while the key is absent; an unknown key is an error that names it.

| Key | Type | What it is |
| --- | --- | --- |
| `areas` | list of folder names | the top-level folders that hold notes |
| `system_folders` | list of folder names | the other folders allowed at the root, such as `templates` |
| `root_allowlist` | list of patterns | the files allowed at the root, such as `Home.md` or `*.base` |
| `states` | list of strings | the values `status` may take on every note |
| `states_by_type` | object, a list per `type` | the extra states one `type` may take, such as `draft` on meetings |
| `kinds` | object, an entry per kind | the kinds of note, below |
| `view_properties` | list of keys | the properties that views and `.base` files read |
| `allowed_keys` | list of keys | the other frontmatter keys a typed note may carry |
| `size_cap_bytes` | integer | the size above which a note gets a WARN |
| `size_skip_folders` | list of paths | the folders the cap and `split.py` skip |
| `size_skip_types` | list of `type` values | the types the cap and `split.py` skip |
| `size_skip_names` | list of patterns | the note names the cap and `split.py` skip |
| `date_keys` | list of keys, optional | the frontmatter keys whose value must be an unquoted `YYYY-MM-DD` date |
| `tag_pattern` | regular expression, optional | the form every entry of `tags` must match in full, such as `[a-z0-9]+(-[a-z0-9]+)*` |
| `warn_words` | list of words, optional | the words that get a WARN in a `description`, matched as whole words in any case |

A kind's entry:

| Key | Type | What it is |
| --- | --- | --- |
| `has_status` | boolean, required | its notes carry `status`; its template gets `status: planned` |
| `hub` | boolean, required | its notes live in a folder of their own name, `<name>/<name>.md` |
| `note_type` | string, default the kind's name | the `type` its notes carry; `season` may make `type: project` notes |
| `required` | list of keys, default none | the keys its notes must fill |
| `progress` | `"checkboxes"` or `null` | its notes carry `progress`, counted from their checkboxes |
| `template` | path or `null` | the template `new.py` fills and `scaffold.py` creates |
| `folder` | folder name or `null` | the folder inside an area that holds its notes |
| `name_pattern` | regular expression or `null` | the form its notes' names must match in full, without `.md`, such as `\d{4}-\d{2}-\d{2} .+` |

## Commands

Each command takes `--vault PATH`. `check.py` falls back to `MIMIR_VAULT`,
then the current directory; the others use the current directory.

| Command | Does |
| --- | --- |
| `check.py [--fix] [--report-only]` | prints one `LEVEL rel: message` line per finding; exits 1 on an ERROR unless `--report-only`, 2 on a config error |
| `new.py KIND NAME --area AREA [--parent NAME]` | creates the note and prints its path; `--parent` sets `project: "[[NAME]]"` |
| `split.py NOTE [--level N] [--keep HEADING] [--dry-run] [--force]` | moves the note to `<dir>/<name>/<name>.md` and splits it there, at the lowest heading depth that repeats or at `--level`; the sections named with `--keep` (for a plan, those before the first `Phase` or `Task` heading) stay in the hub; refuses a note under the cap without `--force`, a skipped note and an active plan |
| `scaffold.py [--dry-run]` | creates each missing template and never touches an existing file |

The scripts sit in `skills/mimir/scripts/`. Run them as `python3 -B`.

## Tests

The scripts have unit tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s skills/mimir/tests
```

`evals/` holds cases for `claude plugin eval`: filing a loose note,
starting a project, splitting a long plan and a `progress` that is off.
They run against a model, on your own account:

```sh
claude plugin eval . --runs 3 --no-publish
```

CI checks the manifests, the tests, the Markdown, and that no em dash
slipped in.

## License

[MIT](LICENSE)
