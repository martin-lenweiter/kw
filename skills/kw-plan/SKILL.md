---
name: kw-plan
description: Gate 1 of a kw run. Clarify a brief with the user, draft a task plan with testable done_when criteria, get it critiqued in a fresh context, and hand it to the user for approval. Use when starting a kw run or when a run is in phase clarifying, awaiting-answers, planning or awaiting-approval.
---

# kw plan (gate 1)

A run is a directory. Change state only with the `kw` command; never edit
`state.json` or `ledger.jsonl`. Run `kw status <run>` first and follow `next`.

## 1. Start

- New work: `kw init <run> --brief <file>` or write `<run>/brief.md` after init.
- Existing run: `kw status <run>`.

## 2. Clarify (phase `clarifying`)

Read `brief.md` and any files it names. List what would change the result if
guessed wrong: scope, inputs, output format, sources allowed, costs or credits,
write permissions, and what counts as done.

- If anything is open, write numbered questions with a recommended default
  each to `<run>/questions.md`, run `kw phase <run> awaiting-answers`, and ask
  the user. Stop until answered.
- When answers arrive, append them under each question, then
  `kw phase <run> planning`.
- Ask only questions whose answer changes the plan.

## 3. Plan (phase `planning`)

Write `<run>/plan.md` (approach, inputs, method, costs, risks) and
`<run>/tasks.json`:

```json
[{"id": "t01", "goal": "…", "done_when": "checkable condition", "inputs": "…"},
 {"id": "t02", "goal": "…", "done_when": "…", "depends_on": ["t01"]}]
```

Exactly one task must have `"acceptance": true`. It depends (directly or
through other tasks) on every other task, and its `done_when` is the brief's
acceptance criteria applied to the whole result: outputs integrate, totals
agree, and every brief requirement is met. kw rejects a plan without it, and a
run only ends as `done` when this task is verified.

`depends_on` is optional for other tasks. A task runs once all its dependencies are verified
or needs-human; it then receives their outputs and statuses.

Rules:
- Each task is independent enough to run in its own subagent.
- `done_when` must be checkable from the output alone by someone who did not do
  the work. Bad: "good coverage". Good: "each row has phone in E.164 with a
  source URL, or status not_found with the sources tried".
- Size tasks so one attempt fits in one agent session.

Load with `kw tasks set <run> <run>/tasks.json`.

## 4. Critique

Spawn a fresh agent (subagent, or `claude -p` / `codex exec`) with only
`brief.md`, the answers, `plan.md` and `tasks.json`. Ask it to report in
`<run>/critique.md`: brief items not covered, scope added beyond the brief,
`done_when` that cannot be checked, overlapping or oversized tasks. Revise once
if it finds blocking issues, and note what changed.

## 5. Approval

`kw phase <run> awaiting-approval`, then show the user a short summary: task
count, approach, costs, critique outcome. Only the user approves:
`kw approve <run>`. After approval the plan and `done_when` are frozen.
