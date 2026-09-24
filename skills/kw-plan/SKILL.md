---
name: kw-plan
description: Plan a new kw run or continue one in clarifying, awaiting-answers, planning, or awaiting-approval. Define outcomes and assignments, obtain an independent critique, and request user approval.
---

# Plan

KW adds shared state around planning, implementation, and verification. Give
capable agents clear outcomes and room to choose methods. Use `kw` for state
changes, never edit `state.json` or `ledger.jsonl`.

Start with `kw status <run>` and follow `next`. For new work, use
`kw init <run> --brief <file>`.

## Clarify the outcome

Read the brief and its relevant inputs. Ask only about missing information that
materially changes the result or authority: scope, deliverables, sources, costs,
and external writes. Record questions and answers in `questions.md`. If an
answer is required, run `kw phase <run> awaiting-answers` and wait. Once the
brief is clear, run `kw phase <run> planning`.

## Make coherent assignments

Write `plan.md` with the approach, constraints, agent allocation, verification
approach, and material costs or risks. Write `tasks.json`, for example:

```json
[
  {"id": "research", "goal": "Find candidate partners", "done_when": "Each candidate has a source and a reason for inclusion", "inputs": "brief.md"},
  {"id": "deliver", "goal": "Produce the combined recommendation", "done_when": "The deliverable meets the brief and accounts for research findings", "depends_on": ["research"], "acceptance": true}
]
```

- Assign complete outcomes. Split work when independence, ownership, or useful
  parallelism justifies it. Avoid prescribed thinking steps.
- Make `done_when` checkable against outputs and sources by an independent
  reviewer. Choose output formats that serve the task.
- Exactly one `acceptance` task must depend, directly or indirectly, on every
  other task. It produces the combined deliverable; its criteria cover the
  original brief. It is still implementation, not self-verification.
- Default to one global independent verification after implementation. For
  genuinely large work or a consequential dependency, set `checkpoint: true`
  on the relevant task and explain why. Its consumers wait for verification;
  ordinary consumers can start when their inputs are completed.
- Optional `model` names an actual model ID; `effort` is separate. Otherwise use
  runtime defaults. Choose for the task, without fixed model tiers.
- Declare shared surfaces in `uses` only where useful. Set capacities with
  `kw init --resource <name>=<limit>` and overall `--max-parallel`. Respect
  permissions and quotas; changing runtimes does not authorize bypassing them.

Load the tasks with `kw tasks set <run> <run>/tasks.json`.

## Critique and approval

Ask a fresh agent to critique coverage, unnecessary scope, acceptance criteria,
and decomposition using the brief, answers, plan, and tasks. Save its findings
in `critique.md` and resolve material blockers before approval.

Record in `plan.md` and show the user:

- Deliverables, task groups, and the global verification or checkpoint approach.
- Whether execution uses subagents, separate processes, or the current agent.
  Identify delegated tasks and the orchestrator's work; “in parallel” alone is
  insufficient.
- Agent roles, runtime, concrete model, effort where configurable, and maximum
  concurrency. Resolve runtime defaults where possible and label unknowns.
- Costs, permissions, material risks, and the critique outcome.

Run `kw phase <run> awaiting-approval`. Only the user approves with
`kw approve <run>`. Approval freezes task goals and `done_when`; implementation
methods remain flexible within that scope.
