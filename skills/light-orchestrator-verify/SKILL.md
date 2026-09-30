---
name: light-orchestrator-verify
description: Independently verify a light-orchestrator run's combined result, or a planned checkpoint, in phase verifying. Record task verdicts and findings, then close the round.
---

# Verify

Work in a fresh context, independent of the workers. Read the brief, the
answers, `decisions.md`, `plan.md`, the task definitions, and the outputs.
`light-orchestrator status <run>` lists the tasks that await a verdict.

## Check the result

Check the combined result against the brief and the user's decisions, then
assign each defect to the task whose `done_when` it breaks. Perform the check
that each `done_when` describes, and use your judgment for anything it leaves
open.

- Examine the actual result, not the worker's report of it.
- A check you could not perform is not a pass. Record why it was not possible.
- Do not lower the bar, and do not add requirements that the brief and the
  task do not contain.
- Do not give verdicts on `"checkpoint": "human"` tasks; the user reviews
  those.

Review inputs before their consumers, and refresh `status` after each verdict:
a failure invalidates downstream outputs, which must run again before they get
a verdict. At an intermediate checkpoint, judge only the completed work.

## Record verdicts

Write findings when needed:

```json
[{"key": "wrong-total", "severity": "blocking", "text": "The page shows 12.90 for item A; the provider shows 13.90."}]
```

`blocking` means a requirement is unmet; `note` is an improvement outside the
requirements. Reuse a `key` when the same defect persists.

```sh
light-orchestrator verdict <run> <id> pass [--findings <file>]
light-orchestrator verdict <run> <id> fail --findings <file>
```

When every agent-reviewed task has a verdict, run
`light-orchestrator finish <run>`. It sends failures back for repair,
continues after checkpoints, or ends the run as `done` or `partial`. While a
human review is pending, it stops and names the review; run it again after
the user's verdict. The final report states the outputs, the verification
results, and the unresolved work truthfully.
