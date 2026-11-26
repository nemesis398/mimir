---
type: llm
weight: 1
---

The response:

- names `Garden/meetings/Tomato club 2026-11-20.md` as the new place, from
  the `meeting` kind's `folder` and the `Garden` area;
- moves it with the Obsidian CLI, `obsidian move path=... to=...` with the
  app running, not with `mv` or another shell move;
- keeps the note's text as it is;
- runs `check.py` afterwards, or says it would.

It fails when it moves the note with `mv`, `cp` and `rm`, or a rename in
the shell.
