---
name: lo-plan
description: Plan a new lo (light-orchestrator) run or continue one in clarifying, awaiting-answers, planning, or awaiting-approval. Define tasks and how each is checked, obtain an independent critique, and request user approval. Use when the user asks to use lo or light-orchestrator, or for a large multi-step job that benefits from tracked tasks and independent checking.
---

# Plan

`lo` keeps the state of a multi-step job outside your context:
what was agreed, what is done, what is checked, and what changed. You choose
the approach, the task split, the checks, and the models. Change state only
through the `lo` CLI; never edit `state.json` or
`ledger.jsonl`.

Start with `lo status <run>` and follow `next`. For new work, write the
user's request to a brief and run `lo init <run> --brief <file>`. Unless the
user names a place, use `.lo/<short-name>` in the working directory as the run,
and in a git repository add `.lo/` to `.git/info/exclude` so run state is not
committed.

## Clarify

Ask only about missing information that changes the result or your authority,
such as scope, costs, and external writes. Ask the user in the conversation
and record questions and answers in `questions.md`. In a headless run, run
`lo phase <run> awaiting-answers` and stop. When the brief is
clear, run `lo phase <run> planning`.

## Plan the tasks

Write `plan.md` with the approach and anything workers and the verifier need
to know. `plan.md`, the tasks, and `decisions.md` are the run's one source of
truth; keep them current as decisions change. If the user's environment has a skill that defines good work of this
kind, name it in `plan.md` so that workers and the verifier apply it.

Write `tasks.json`, for example:

```json
[
  {"id": "data", "goal": "Find how to get prices from each provider", "done_when": "Each provider has a working fetch shown with one real result, or a documented reason it is blocked"},
  {"id": "design", "goal": "Design the main page", "done_when": "The user approved a screenshot of the page", "depends_on": ["data"], "checkpoint": "human"},
  {"id": "deliver", "goal": "Ship the site", "done_when": "Tests pass and the live site shows real prices for three restaurants", "depends_on": ["design"], "acceptance": true}
]
```

- Write each `done_when` as the check an independent verifier performs: what
  to run, open, or read, and the expected result.
- Exactly one `acceptance` task depends, directly or indirectly, on every other
  task and covers the whole brief.
- Set `"checkpoint": true` when consumers must wait until an input is
  verified. Set `"checkpoint": "human"` when only the user can judge the
  result, for example a design or a blocked data source. The run pauses there
  for the user's verdict.
- Set `model` and `effort` for each task. The planner, orchestrator, and
  verifier use the most intelligent model available, as do tasks that need
  hard judgment. Implementers use a capable mid-tier model, never the least
  capable tier.
- Declare shared surfaces in `uses` and their capacity with
  `lo init --resource <name>=<limit>` only where parallel
  tasks would conflict.

Load the tasks with `lo tasks set <run> <run>/tasks.json`.

## Critique and approval

Ask a fresh agent to critique the plan against the brief: missing coverage,
unnecessary tasks, and checks that would not catch a bad result. Save its
findings in `critique.md` and resolve material issues.

Show the user the deliverables, how each is checked, the human checkpoints,
the agents and models, and any costs or external writes. Then run
`lo phase <run> awaiting-approval` and ask for approval. Only the user
approves: when the user approves in the conversation, run `lo approve <run>`
and continue with the lo-run skill in the same conversation. Approval freezes
task goals and `done_when`; only the user changes them later, through
`amend`.
