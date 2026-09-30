# lo (light-orchestrator)

`lo` keeps the state of a multi-step agent job outside the
model: what was agreed, what is done, what is checked, and what changed. The
model does not have to remember the plan, the progress, or a requirement the
user added halfway through; it reads the current state and the next action.

It works for any multi-step job, such as research, a dataset, or a software
build, inside an existing agent environment such as Claude Code, Codex, or
Hermes. That environment supplies models, tools, and service access.

## Design principle: delete first

We rely on the planner's and orchestrator's intelligence. They choose the
approach, the task split, how each task is checked, where the user must
review, and which models to use. The tool fixes only what must not vary:

- shared state that one CLI changes, so parallel agents do not collide and a
  run survives a crash;
- an independent verifier that checks the result against what was asked;
- the user's approval of the plan and of later scope changes.

We add guidance or code only when a capable model fails without it. When a
model improves, we remove guidance that it no longer needs.

## How a run proceeds

| Stage | What happens |
|---|---|
| Plan | The planner asks what it must, splits the job into tasks, and writes for each task how an independent verifier will check it. A fresh agent critiques the plan. You approve it. |
| Implement | Workers claim tasks, run in parallel where tasks are independent, and record their outputs. |
| Verify | A fresh verifier checks the combined result and records a verdict per task. Failures go back for bounded repair. |

A task can be a checkpoint. With `"checkpoint": true`, its consumers wait until
the verifier passes it. With `"checkpoint": "human"`, the run pauses until you
review it, for example to approve a design:

```sh
lo verdict <run> design pass
lo verdict <run> design fail --note "simpler layout, larger prices"
```

A rejected task goes back to its worker with your note.

When you add or change something during a run, the orchestrator records it
with `lo amend`. The run reopens only the affected tasks.

## Setup

`lo` is one Python 3 script with no third-party dependencies.
Put it on your PATH, for example:

```sh
ln -s "$PWD/lo.py" ~/.local/bin/lo
lo --help
```

Give your agent the four skills in [`skills/`](skills): plan, run, verify,
and graph.

## Start a run

Describe the result you want to your agent:

> Use lo-plan to build a site that compares fast-food prices
> across delivery apps. Show me the design before building the rest.

Or run it headless from the terminal. `loop` drives separate agent processes
and pauses for answers, plan approval, and human reviews:

```sh
lo init runs/prices --brief brief.md --max-parallel 4
lo loop runs/prices --harness claude   # or --harness codex
lo status runs/prices
lo graph runs/prices
```

After a pause, answer `questions.md` and run
`lo phase runs/prices planning`, or approve with
`lo approve runs/prices`, or give a review verdict, then run
`loop` again. `--timeout` sets the limit per agent call (default one hour).
`<run>/loop.json` overrides the harness command.

## Run files

Change state only through the CLI; never edit `ledger.jsonl` or `state.json`.

| File or folder | Contents |
|---|---|
| `brief.md`, `questions.md` | Request and answers |
| `plan.md`, `tasks.json`, `critique.md` | Approved approach, tasks, and critique |
| `decisions.md` | Your decisions during the run |
| `out/<task>/<attempt>/` | Output of each attempt |
| `report.md` | Final status, outputs, findings, and unresolved work |
| `logs/` | Agent process logs |
| `ledger.jsonl`, `state.json` | Progress history and current state |

## Reference

**Models.** A task can set a concrete `model` ID and a separate `effort`;
otherwise it inherits the runtime default. `loop --model` and `--effort` set
run-wide values.

**Concurrency.** `--max-parallel` caps concurrent workers. `--resource
browser=1` limits tasks that declare the resource in `uses`. Limits do not
grant permissions or quota.

**Dependencies.** Ordinary inputs must be completed; checkpoint inputs must be
verified. A blocked input blocks its consumers.

**Recovery.** Each claim has a token and its own output path, so an old worker
cannot complete a newer attempt. `resume` marks expired claims for attention;
it does not show whether an external write failed. `resolve` returns a
`needs-human` task after a fix, or with `--retry` for a new attempt.
`resume --force` releases running claims only after you stop their workers.

**Repairs.** Verification findings drive repairs, with a cap per task and per
run. A defect that repeats stops the task as `needs-human`.
