---
type: llm
weight: 1
---

The response:

- does not split the plan now, because its `status` is `active`, and says
  that `split.py` refuses an active plan;
- offers to split it once the plan is done or paused, or asks the user to
  change the status first, at the phase level (`--level 2`) with
  `--dry-run` first;
- says the text moves into the parts unchanged and the hub keeps the path.

It fails when it runs or proposes `split.py` on the active plan, or rewrites
or shortens the plan's text to bring it under the cap.
