# kw

A small plan → execute → verify layer for agent knowledge work: research,
enrichment and pipelines. It runs on top of any harness that can read files
and run shell commands (Claude Code, Codex, Hermes). It adds no harness of its
own.

## Parts

- `kw.py`: state CLI. One file, stdlib only. Agents change run state only
  through it.
- `skills/kw-plan`: gate 1. Clarify the brief, plan tasks with testable
  `done_when`, get a fresh-context critique, and get user approval.
- `skills/kw-run`: gate 2. The orchestrator claims tasks and runs each in a
  subagent.
- `skills/kw-verify`: gate 3. A fresh-context verifier records pass or fail
  with keyed findings.

## Run directory

```
<run>/brief.md  questions.md  plan.md  tasks.json  critique.md
<run>/ledger.jsonl   append-only event log (source of truth)
<run>/state.json     snapshot, rebuilt from the ledger when missing or stale
<run>/out/           task outputs
<run>/report.md
```

## Phases

`clarifying → awaiting-answers → planning → awaiting-approval → executing ⇄ verifying → done | partial`

Only `kw approve` starts execution. The plan and `done_when` are frozen after
approval.

## Dependencies

A task may list `depends_on`. It becomes claimable once every dependency is
`verified` or `needs-human`, and its claim includes their outputs. A stage
that only waits for dependencies does not use a repair round.

## Acceptance task

Each task is verified on its own. To check the whole result against the brief,
a plan must contain exactly one task with `"acceptance": true` that depends,
directly or transitively, on every other task. A run ends as `done` only when
the acceptance task is verified; otherwise it ends as `partial`.

## Repair loop limits

A failed task returns to `todo` for repair until one of these happens. Then it
becomes `needs-human` and the rest of the run continues:

- The same blocking finding `key` repeats after a repair (stall).
- The task has used `max_repairs` repairs (default 2).
- The run has used `max_rounds` verify rounds (default 3).

## Restart safety

- Each event is fsynced to the ledger before the snapshot is replaced
  atomically.
- A torn last ledger line is ignored.
- A claimed task holds a lease. `kw resume` returns expired tasks to `todo`
  without counting an attempt.
- A file lock serialises all commands, so parallel workers cannot lose updates.

## Test

```
python3 -m unittest test_kw
```
