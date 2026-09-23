---
name: kw-verify
description: Gate 3 of a kw run. Independently verify each executed task against its frozen done_when criteria, record pass or fail with keyed findings, and close the round so failures go back for bounded repair. Use when a kw run is in phase verifying.
---

# kw verify (gate 3)

Work in a fresh context. Read only `brief.md`, the answers in `questions.md`,
`tasks.json` and the outputs. Do not read the executors' conversations.

For each task with status `done` (`kw status <run>`):

1. Check the output against that task's `done_when`, and spot-check facts:
   open cited sources, confirm values are present where claimed, check formats.
2. Write findings to a JSON file:

```json
[{"key": "phone-without-source", "severity": "blocking", "text": "rows r12, r19: phone has no source URL"}]
```

   - `blocking`: `done_when` is not met. Only these send the task back.
   - `note`: improvements outside `done_when`. They go to the report.
   - `key`: a short stable slug for the problem. Reuse the same key if the same
     problem is still there after a repair; kw uses it to detect stalls.
3. `kw verdict <run> <id> pass [--findings f.json]` or
   `kw verdict <run> <id> fail --findings f.json`.

You judge only against the frozen `done_when`. Do not add requirements.

- Check every row or item against every clause that can be checked
  mechanically, by script, not by reading a sample. Spot-check facts on a
  sample.
- Any violation of a `done_when` clause is `blocking`, however small. Do not
  downgrade it to a note.
- On a repair round, read the previous findings first and reuse the same `key`
  for a problem that is still present. Write the new findings to a new file.
- Work that is described but was not actually done (for example, "search
  unavailable") does not satisfy a clause.

When every task has a verdict: `kw finish <run>`. It sends failed tasks back
for repair, or ends the run as `done` or `partial`. Tasks stop as
`needs-human` when the same blocking finding repeats, the per-task repair cap
is reached, or the run round cap is reached.

## Report

When the run ends, write `<run>/report.md`: result summary, verified outputs,
`needs-human` tasks with their findings and what was tried, and notes.
