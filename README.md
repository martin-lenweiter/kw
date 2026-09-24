# kw

KW adds lightweight scaffolding and shared state around **planning,
implementation, and verification**. It helps agents divide work, run independent
tasks in parallel, and check the combined result.

We trust capable models to choose their methods. Following the bitter lesson,
we prefer improvements in model capability over accumulating hand-written
process. KW is not a workflow engine. We add small safeguards only when they
address concrete failures.

Use KW for research, comparisons, dataset enrichment, and other knowledge work.
It works with an existing agent environment such as Claude Code, Codex, or
Hermes. That environment supplies models, tools, context management, and
service access; KW supplies the shared record of what was agreed and completed.

## Start with a brief

Give your agent the [kw-plan skill](skills/kw-plan/SKILL.md) and describe the
result you want:

> Use kw-plan to research 50 potential distribution partners. Include a source
> and a reason for each recommendation. Do not contact anyone or use paid tools.

The planner clarifies material unknowns, proposes coherent assignments, and
gets an independent critique. Before approval, it shows the deliverables,
verification approach, costs and permissions, and which tasks use subagents or
separate processes. It also discloses runtime, model, effort where configurable,
and maximum concurrency. Approval freezes the task goals and completion criteria.

## How work proceeds

| Stage | Responsibility |
| --- | --- |
| Plan | Agree on the goal, constraints, assignments, and verification approach. |
| Implement | Complete assignments and integrate the result; parallelize independent work when useful. |
| Verify | Independently check the combined result against the approved goal. |

Global verification is the default for new runs: completed implementation tasks unlock their
dependents without a separate review at each step. The final acceptance task
produces the combined deliverable; a fresh verifier then assesses the whole
result and records task verdicts in one context.

For genuinely large work or consequential dependencies, the planner or
orchestrator can identify an intermediate checkpoint during planning. A task
marked `"checkpoint": true` must pass verification before its consumers start.
Explain why the checkpoint is useful; it is not a routine ceremony. Existing
runs keep their original verification behavior.

Failed checks return focused findings for bounded repair. Unresolved work ends
as `partial`; `done` requires verified acceptance and no unresolved tasks.

## Setup

KW is one Python 3 script with no third-party Python dependencies. Make it
available as `kw` on your PATH. For example, from this checkout, if
`~/.local/bin` is on your PATH:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/kw.py" ~/.local/bin/kw
kw --help
```

Make the three skills available to your agent, or point it directly to
[plan](skills/kw-plan/SKILL.md), [run](skills/kw-run/SKILL.md), and
[verify](skills/kw-verify/SKILL.md).

## Run from the terminal

`kw loop` coordinates separate agent processes without an open chat session.
It requires an installed, authenticated Claude Code or Codex CLI.

```sh
kw init runs/partner-research --brief brief.md --max-parallel 4
kw loop runs/partner-research --harness claude
kw status runs/partner-research
kw graph runs/partner-research   # task graph by dependency level, with the current phase
```

Use `--harness codex` for Codex. The loop pauses for answers or plan approval.
Record requested answers in the run's `questions.md`, then continue planning:

```sh
kw phase runs/partner-research planning
kw loop runs/partner-research --harness claude
```

Read `plan.md`, `tasks.json`, and `critique.md`. When you approve:

```sh
kw approve runs/partner-research
kw loop runs/partner-research --harness claude
```

The loop implements, verifies, and handles repair rounds. Logs are in `logs/`.
`--timeout 3600` sets the default one-hour limit per agent call.

## Shared state and controls

The CLI is the standard way to read and change run state. Do not edit
`ledger.jsonl` or `state.json` directly.

| File or folder | Contents |
| --- | --- |
| `brief.md`, `questions.md` | Request and answers. |
| `plan.md`, `tasks.json`, `critique.md` | Approved approach, assignments, and critique. |
| `decisions.md` | User decisions during execution. |
| `out/<task>/<attempt>/` | Output for each execution attempt, with links to any supporting artifacts. |
| `report.md` | Final status, outputs, verification findings, and unresolved work. |
| `logs/` | Agent process logs. |
| `ledger.jsonl`, `state.json` | Progress history and current state. |

**Models.** The planner chooses suitable agents; KW has no fixed worker model
policy. A task can name an actual `model` ID and a separate `effort`. Omitted
values inherit runtime configuration. The plan must disclose the selected
runtime and configuration, including any unresolved choice. Use `kw loop --model <id> --effort <level>` for run-wide settings.
Loop command overrides live in `<run>/kw-loop.json`. Legacy tier names remain
compatible with old runs; new plans should use concrete model IDs.

**Concurrency.** `--max-parallel` caps concurrent workers. Use a resource limit,
such as `--resource browser=1`, when tasks share a surface that requires it;
tasks declare this in `uses`. Capacity limits do not grant permission, credits,
or extra API quota.

**Dependencies.** Ordinary `depends_on` inputs must be completed; checkpoint
inputs must be verified. A blocked required input blocks its consumers. Do not
present incomplete downstream work as successful delivery.

**Recovery.** Each claim has a unique token and output path. Completion must
include that token, so an old worker cannot complete a newer attempt. Inspect
logs before retrying failed processes. `kw resume <run>` marks expired
claims as needing attention; it does not prove that an external write failed. Check the destination
before repeating publication and keep one designated writer.

**Requirement changes.** Only the user changes approved scope. Record the
change with `kw amend <run> <file> --note "requested change"`. The file lists
new tasks, revised fields for existing tasks, and `{"id": ..., "drop": true}`
for tasks that are no longer required. kw reopens the revised tasks,
their dependents, and the acceptance task. Other verified work stays verified.
A finished run returns to `executing`. The ledger keeps the original plan
and each amendment.

**Repairs.** Verification findings drive focused repair, with bounded retries.
After an authorized fix to a `needs-human` task, use
`kw resolve <run> <task> --note "what was fixed" --output <path>` to return it
for review. If execution failed before producing a result, stop the old worker,
check external writes, then use `kw resolve <run> <task> --retry --note "reason"`.
`kw resume --force` releases running claims only after you have stopped their
workers and reconciled external writes.

A damaged ledger stops with a diagnostic, including an incomplete final record.
KW preserves the file for deliberate recovery rather than silently dropping history.

## Development

Instructions live in `skills/`; state and loop commands live in `kw.py`.
Keep changes small and test concrete behavior:

```sh
python3 -m unittest test_kw
```
