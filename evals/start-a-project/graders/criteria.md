---
type: llm
weight: 1
---

The response:

- uses `new.py project "Raised beds" --area Garden`, with `--vault
  /home/alice/Notes` or from inside the vault;
- gives the path `Garden/Raised beds/Raised beds.md`, a folder with its hub,
  because the `project` kind has `hub: true` and no `folder`;
- says the note comes from `templates/Project.md`, and that `new.py` refuses
  when the name exists anywhere in the vault or when the Obsidian app does
  not answer.

It fails when it writes the note by hand with a shell redirect or a file
write instead of `new.py`, or puts it at the vault root.
