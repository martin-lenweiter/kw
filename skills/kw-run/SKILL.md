---
name: kw-run
description: Gate 2 of a kw run. Orchestrate execution of an approved plan by claiming tasks and running each in a subagent, including repair rounds after verifier findings. Use when a kw run is in phase executing.
---

# kw run (gate 2)

Change state only with `kw`. Start with `kw resume <run>` (releases tasks left
running by a crash) and `kw status <run>`.

## Loop

1. `kw claim <run> --owner <agent-id>` returns a task with `goal`, `done_when`,
   `attempts`, `last_findings` and `dependencies` (status and output of each
   task it depends on), or `claimed: null` when nothing is runnable.
2. Give the task to a subagent with only: the task JSON, `brief.md`, `plan.md`,
   the answers in `questions.md`, and the relevant inputs. On a repair
   (`attempts > 0`) include `last_findings` and say: fix every blocking finding;
   do not redo passing work.
3. The subagent writes `<run>/out/<id>.<ext>` (write to a temp name, then
   rename). For row-based work, write one JSON line per row keyed by a stable
   record ID and skip rows already present, so a restart does not redo them.
4. `kw done <run> <id> --output out/<id>.<ext>`.

Run independent tasks in parallel when the harness supports subagents. Without
subagents, run them one after another. Keep paid calls and external writes in
one place (this orchestrator or one designated worker) unless the plan says
otherwise.

When `claim` returns null and `kw status` shows no running tasks:
`kw phase <run> verifying` and hand over to kw-verify. Tasks waiting on
dependencies become runnable after the verify round that settles them.

## Models and capacity

`claim` returns the task's `model` tier and `uses`. Map the tier to your
harness: for example in Claude Code `fast` = Haiku, `standard` = Sonnet,
`strong` = Opus; in Codex use the matching reasoning level. `claim` holds a
task back while its resources are at capacity (`waiting_on_capacity`); claim
again when a running task finishes.

Capacity limits concurrency, not quotas. If a shared quota runs out (for
example the harness's web-search budget), do not let workers mark work as
unavailable: pause those tasks or start them on another harness
(`codex exec`, `claude -p`) with its own quota.

## Rules

- Follow the frozen plan. If a task cannot be done as specified, finish it with
  what is possible and state the gap in the output; the verifier decides.
- Never change `done_when` or add tasks.
- A crash loses at most the in-flight tasks; their leases expire and they
  return to todo without counting as a repair attempt.
