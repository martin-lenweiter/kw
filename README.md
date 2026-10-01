# lo (light-orchestrator)

`lo` is a shared to-do board for a team of AI agents working on one job. It
records what was agreed, what is done, what was checked, and what changed, in
files outside any agent's context. Agents read the board instead of
remembering, so a long or parallel job stays on track even when you change
your mind halfway through.

It is not an agent itself. It runs inside an agent environment such as Claude
Code, Codex, or Hermes, which supplies the models and tools. `lo` supplies the
board and three short skills that tell agents how to use it.

## Why

- **Less for the model to remember.** The plan, the progress, and every
  decision live in the run folder. When you add a requirement mid-run, it is
  written there, and every agent sees it.
- **One source of truth.** The orchestrator, the workers, and the verifier
  read the same plan and decision log. A change raised by you or discovered by
  an agent goes into that record; minor questions the orchestrator decides and
  logs, material ones come to you.
- **Independent checking.** A fresh agent that did not do the work checks the
  result against what you asked for.

## Design principle: delete first

We rely on the agents' intelligence. The planner and orchestrator choose the
approach, the task split, how each task is checked, where you review, and
which models to use. `lo` fixes only what must not vary: the shared state, the
independent check, and your approval of the plan and of scope changes. We add
a rule or a feature only when capable models fail without it, and remove it
when they no longer need it.

## Using it

Open a chat in the folder or repository you want to work in and say:

> Use lo to build a site that compares fast-food prices across delivery apps.
> Show me the design before building the rest.

The agent then:

1. creates a run folder (`.lo/<name>`, hidden from git), asks what it needs,
   writes a plan in which every task says how it will be checked, has a fresh
   agent critique the plan, and asks for your approval;
2. after you approve in the chat, runs the tasks, in parallel where they are
   independent;
3. stops at each point where you asked to review, shows you the result, and
   records your answer;
4. has a fresh agent verify the combined result, repairs what fails, and
   reports what was delivered, how it was checked, and what is unresolved.

You answer questions and approve in the conversation; you do not need to type
commands. The chat agent is the orchestrator; it hands tasks to subagents.

To run unattended, start the same agent without a chat and point it at the
run, for example `claude -p "Continue the lo run in .lo/prices with lo-run"`
or `codex exec`. It stops where it needs you, and all state stays in the run
folder, so any later chat can pick it up.

## How a run works

| Stage | What happens |
|---|---|
| Plan | The planner clarifies the request, splits it into tasks, and writes each task's check (`done_when`). A fresh agent critiques the plan. You approve it. |
| Implement | Workers claim tasks and record their outputs. A task starts when its inputs are done. |
| Verify | A fresh verifier checks the combined result and gives each task a verdict. Failed tasks go back for a bounded number of repairs. |

**Checkpoints.** A task marked `"checkpoint": true` must pass verification
before the tasks that depend on it start. A task marked
`"checkpoint": "human"` waits for your verdict, for example on a design:

```sh
lo verdict <run> design pass
lo verdict <run> design fail --note "simpler layout, larger prices"
```

**Changes and decisions.** When you change the request, the orchestrator
records it with `lo amend`, which reopens only the affected tasks. Workers
report the decisions they make under `## Decisions` and the questions they
cannot settle under `## Needs decision` in their results. The orchestrator
records decisions in `decisions.md`, settles minor questions itself, and
brings material ones to you.

**End states.** A run ends `done` when the final (acceptance) task passes and
nothing is unresolved, otherwise `partial`, with the open items in
`report.md`.

## Parts

| Part | What it is |
|---|---|
| `lo` (`lo.py`) | The command-line tool that owns the run state. Every change goes through it, so parallel agents cannot overwrite each other and a run survives a crash. One Python 3 file, no dependencies. |
| `skills/lo-plan` | How to plan a run: clarify, write tasks with checks, get a critique, ask for approval. |
| `skills/lo-run` | How to coordinate the work: claim tasks, brief workers, keep the plan and decisions current, record changes, handle problems. |
| `skills/lo-verify` | How to check the result independently and record verdicts. |
| Run folder | The board itself; see below. |

## Run folder

| File | Contents |
|---|---|
| `brief.md`, `questions.md` | Your request, and the questions with their answers |
| `plan.md`, `tasks.json`, `critique.md` | The current plan, the tasks with their checks, and the critique |
| `decisions.md` | Every decision made during the run, and who made it |
| `out/<task>/<attempt>/` | Each worker's output, unchanged after completion |
| `reviews/<task>.md` | The verifier's evidence for each task |
| `report.md` | Final status, outputs, findings, and unresolved work |
| `ledger.jsonl`, `state.json` | The full history and the current state; change them only through `lo` |

## Setup

Link the script onto your PATH and give your agent the three skills:

```sh
ln -s "$PWD/lo.py" ~/.local/bin/lo
lo --help
```

## Command reference

| Command | Use |
|---|---|
| `lo init <run> --brief <file>` | Create a run |
| `lo status <run>` / `lo graph <run>` | Current state and next action / task graph |
| `lo phase <run> <phase>` | Move between planning phases |
| `lo tasks set <run> <file>` | Load the planned tasks |
| `lo approve <run>` | Approve the plan (on your instruction) |
| `lo claim`, `lo done`, `lo block` | A worker takes a task, completes it, or reports a blocker |
| `lo verdict <run> <id> pass\|fail` | Record a verdict (`--findings <file>` or `--note <text>`) |
| `lo finish <run>` | Close a verification round |
| `lo amend <run> <file>` | Record a change to the approved tasks |
| `lo resolve <run> <id>` | Return a task that needed you, after a fix or with `--retry` |
| `lo resume <run>` | Recover after a crash; flags expired claims |

**Options.** `init --max-parallel N` caps concurrent workers.
`init --resource browser=1` limits tasks that declare `"uses": ["browser"]`.
`init --max-repairs N` sets repairs per task (default 2); a finding that
repeats stops the task for you. Tasks can set a concrete `model` and a
separate `effort`.

**Recovery.** Each claim has a token and its own output folder, so a stale
worker cannot overwrite a newer attempt. A claim lease lasts 30 minutes.
`resume` flags expired claims but cannot tell whether their external writes
succeeded; check before you retry. `resume --force` releases running claims
only after you have stopped their workers.
