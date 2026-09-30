---
name: light-orchestrator-plan
description: Plan a new light-orchestrator run or continue one in clarifying, awaiting-answers, planning, or awaiting-approval. Define tasks and how each is checked, obtain an independent critique, and request user approval.
---

# Plan

light-orchestrator keeps the state of a multi-step job outside your context:
what was agreed, what is done, what is checked, and what changed. You choose
the approach, the task split, the checks, and the models. Change state only
through the `light-orchestrator` CLI; never edit `state.json` or
`ledger.jsonl`.

Start with `light-orchestrator status <run>` and follow `next`. For new work,
use `light-orchestrator init <run> --brief <file>`.

## Clarify

Ask only about missing information that changes the result or your authority,
such as scope, costs, and external writes. Record questions and answers in
`questions.md`. If you need an answer, run
`light-orchestrator phase <run> awaiting-answers` and stop. When the brief is
clear, run `light-orchestrator phase <run> planning`.

## Plan the tasks

Write `plan.md` with the approach and anything workers and the verifier need
to know. If the user's environment has a skill that defines good work of this
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
- Set `model` and `effort` for each task. Use a large model for work that
  needs judgment; implementers use a medium model, never a small one. The
  planner, orchestrator, and verifier run on large models.
- Declare shared surfaces in `uses` and their capacity with
  `light-orchestrator init --resource <name>=<limit>` only where parallel
  tasks would conflict.

Load the tasks with `light-orchestrator tasks set <run> <run>/tasks.json`.

## Critique and approval

Ask a fresh agent to critique the plan against the brief: missing coverage,
unnecessary tasks, and checks that would not catch a bad result. Save its
findings in `critique.md` and resolve material issues.

Show the user the deliverables, how each is checked, the human checkpoints,
the agents and models, and any costs or external writes. Then run
`light-orchestrator phase <run> awaiting-approval`. Only the user approves,
with `light-orchestrator approve <run>`. Approval freezes task goals and
`done_when`; only the user changes them later, through `amend`.
