# kw

KW helps AI agents carry out research and other knowledge work in three steps:
**plan the work, do it, check the result**. You approve the plan before execution
starts. Separate agents check the outputs against the requirements you agreed on.

Use it for work such as researching companies, comparing suppliers, or enriching
a dataset. It works with an existing agent environment such as Claude Code,
Codex, or Hermes, with access to files and shell commands.

## Start with a brief

Give your agent the [kw-plan skill](skills/kw-plan/SKILL.md) and describe the
result you want. For example:

> Use kw-plan to research 50 potential distribution partners. Include a source
> and a reason for each recommendation. Do not contact anyone or use paid tools.

The agent asks questions where needed, breaks the work into tasks, and gets a
fresh agent to critique the plan. Before asking for approval, it must show you:

- What it will deliver and how each task will be checked.
- Whether it will use subagents or separate agent processes, and their tasks.
- Which models they will use and how many workers will run at once.
- Expected costs, permissions, risks, and the critique outcome.

You can request changes or approve. Approval freezes the plan and each task's
completion criteria, stored as `done_when`.

## Who does the work?

| Role | Job |
| --- | --- |
| Planner | Turns your brief into tasks and asks for approval. |
| Critic | Reviews the plan in a fresh context before you approve it. |
| Coordinator | Assigns ready tasks to workers and tracks progress. |
| Workers | Produce the outputs. Independent tasks can run in parallel. |
| Verifiers | Check outputs against the agreed criteria without reading the workers' conversations. |

In an interactive session, the coordinator uses subagents when available;
otherwise it runs tasks one at a time. The command-line loop below launches
separate agent processes.

Failed checks send a task back for repair. Retries are limited: repeated problems
or exhausted limits become `needs-human`, with findings for you to review.
A final acceptance task checks the combined result against the original brief.
The run is `done` only when that check passes; unresolved work ends as `partial`.

## Setup

KW is one Python 3 script with no third-party Python dependencies. Make it
available as `kw` on your PATH; the agent instructions use that command.
For example, from this checkout, if `~/.local/bin` is on your PATH:

```sh
mkdir -p ~/.local/bin
ln -s "$PWD/kw.py" ~/.local/bin/kw
kw --help
```

Make the three [skills](skills) available to your agent, or point it directly to
their files: [plan](skills/kw-plan/SKILL.md), [run](skills/kw-run/SKILL.md), and
[verify](skills/kw-verify/SKILL.md). Your agent environment provides the models,
tools, and service access needed for the job.

## Run from the terminal

`kw loop` drives the workflow without an open chat session. It requires an
installed, authenticated Claude Code or Codex CLI.

Write your brief in `brief.md`, then start a run:

```sh
kw init runs/partner-research --brief brief.md --max-parallel 4
kw loop runs/partner-research --harness claude
kw status runs/partner-research
```

Use `--harness codex` for Codex. The loop pauses when it needs answers or plan
approval. If it asks questions, record your answers in the run's `questions.md`,
then continue planning:

```sh
kw phase runs/partner-research planning
kw loop runs/partner-research --harness claude
```

Read `plan.md`, `tasks.json`, and `critique.md`. When you approve the plan:

```sh
kw approve runs/partner-research
kw loop runs/partner-research --harness claude
```

The loop runs workers, verifies outputs, and handles repair rounds. Agent logs
are in the run's `logs/` folder. `--timeout 3600` sets a one-hour limit per agent
call; this is the default.

## Where to find the result

Each run has its own folder:

| File or folder | Contents |
| --- | --- |
| `brief.md`, `questions.md` | Your request and answers. |
| `plan.md`, `tasks.json`, `critique.md` | The plan, task definitions, and critique. |
| `decisions.md` | Decisions you make during execution. |
| `out/` | Task outputs. |
| `report.md` | Final summary, verified outputs, and unresolved findings, written by the verifier. |
| `logs/` | Agent logs from `kw loop`. |
| `ledger.jsonl`, `state.json` | Progress history and current state. Use `kw` to change these. |

## Controls and recovery

**Models.** Tasks use `fast`, `standard`, or `strong` tiers. The built-in loop
maps these to Claude's Haiku, Sonnet, and Opus aliases. For Codex, it uses the
configured model with low, medium, or high reasoning. The plan must disclose
the actual worker allocation, not just these tiers. Override loop settings in
`<run>/kw-loop.json`, for example:

```json
{"claude": {"models": {"fast": "haiku", "standard": "sonnet", "strong": "opus"}}}
```

**Concurrency.** Set `--max-parallel` when creating a run. You can also limit
how many worker tasks use a shared tool, such as `--resource clay=1`. Tasks
declare those tools in `uses`. Undeclared resources have no capacity limit;
capacity limits do not set spending or API quotas.

**Dependencies.** A task's `depends_on` list says which tasks must be settled
first. It receives their outputs and statuses. Both `verified` and
`needs-human` dependencies allow downstream work to proceed, so workers must
account for unresolved gaps.

**Repairs.** Defaults allow two repairs per task and three verification rounds.
A repeated blocking finding also stops retries. A task that fails for the first
time still gets one repair. After fixing a `needs-human` task, send it back for
verification with `kw resolve <run> <task> --note "what was fixed"`.

**Interrupted runs.** Progress is saved in the ledger. `kw resume <run>` releases
expired task claims so work can be retried. The loop does this during execution;
if a worker crashes, inspect its log and rerun the loop after its claim expires.
Concurrent state changes are protected by a file lock.

## Development

The workflow instructions live in `skills/`; the state and loop commands live
in `kw.py`. Run the tests with:

```sh
python3 -m unittest test_kw
```
