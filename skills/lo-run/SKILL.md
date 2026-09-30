---
name: lo-run
description: Coordinate implementation of an approved lo (light-orchestrator) plan in phase executing. Claim work, delegate tasks, record outputs, record requirement changes, and handle repairs.
---

# Implement

Change state only through the `lo` CLI. Start with
`lo resume <run>` and `lo status <run>`. When the user asks to see the run,
show the output of `lo graph <run>` in a code block.
Resume marks expired claims for attention; it does not stop old processes or
show whether their external writes succeeded.

## Coordinate work

1. `lo claim <run> --owner <agent-id>` returns a task, its
   dependency outputs, an attempt token, and an output directory, or
   `claimed: null`.
2. Give the worker the task, the relevant decisions, and pointers to inputs.
   On a repair, include the findings and keep passing work.
3. The worker writes its result to `out/<id>/<token>/result.md` (write a temp
   file, then rename) and links supporting evidence.
4. Record completion with
   `lo done <run> <id> --token <token> --output out/<id>/<token>/result.md`
   and check that the command succeeded.

Run independent tasks in parallel where it helps. When a
`"checkpoint": "human"` task is done, show the user its result in the
conversation and ask for a verdict. Record the answer with
`lo verdict <run> <id> pass`, or `fail --note "<feedback>"`, from the
verifying phase, then continue. Use the model and effort
set on each task. When nothing is runnable and no workers remain, run
`lo phase <run> verifying` and hand over to an independent
verifier.

A claim lease lasts 30 minutes; for a longer task, run `resume` and claim
again. For Codex workers, run `codex exec` with the prompt on stdin
(`- < prompt.md`) or with stdin closed, because an open stdin makes it wait.
Capture the final message with `-o <file>`. Stop a worker by its PID, not by a
pattern match on the model name.

## Problems

- If a worker cannot finish, record
  `lo block <run> <id> --token <token> --reason "<what is missing>"`
  instead of completing it. Do not invent results.
- Inspect a failed process before a retry, and do not repeat the same failing
  approach.
- Before you repeat an external write, check the destination for the earlier
  write. A timeout does not show that the write failed.

## Keep the run canonical

`plan.md`, the tasks, and `decisions.md` are the one source of truth for you,
the workers, and the verifier. Every change in requirements or design goes
into them, whether the user raises it or an agent discovers it: update
`plan.md`, record the decision in `decisions.md` with who made it, and amend
the tasks when a goal or `done_when` changes.

Workers report decisions under `## Decisions` and open questions under
`## Needs decision` in their results; copy them into `decisions.md` (the
headless loop does this itself). Decide minor questions yourself and record
them as orchestrator decisions. Bring anything material to the user, such as
scope, product behavior, a design the user will see, costs, or external
writes, and record the answer. Only the user changes approved goals and
`done_when`.

To amend the tasks, write a JSON list of task specs: a new id adds a task, an
existing id revises that task, and `{"id": "<id>", "drop": true}` removes one.
Revise the acceptance task so it covers the change. Run
`lo amend <run> <file> --by <user> --note "<change>"`.

The run reopens the revised tasks, their dependents, and the acceptance task;
other verified work stays verified. Stop workers on the affected tasks first.
If the change adds external writes, costs, or permissions beyond the approved
plan, show it to the user before you record it.

For a `needs-human` task, show the findings to the user. After an authorized
fix, use `lo resolve <run> <id> --note "<fix>"`. For a failed
process that needs a new attempt, stop the old worker, check its external
writes, then use `lo resolve <run> <id> --retry --note "<reason>"`.
