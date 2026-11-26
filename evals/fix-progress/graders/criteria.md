---
type: llm
weight: 1
---

The response:

- explains that `progress` is counted, (`[x]` plus `[-]`) over all
  checkboxes outside fenced code, here 4 of 8, so 50;
- fixes it with `check.py --fix` (with `--vault /home/alice/Notes`), which
  writes only the `progress` line;
- does not tick or untick checkboxes to make the numbers agree.

It fails when it edits `progress` by hand to a value other than 50 or
changes any checkbox.
