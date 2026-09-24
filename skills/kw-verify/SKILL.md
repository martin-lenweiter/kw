---
name: kw-verify
description: Independently verify a kw run's combined result, or a planned intermediate checkpoint, in phase verifying. Record task verdicts and focused findings, then close the round.
---

# Verify

Work in a fresh context, independent of the executors' conversations. Read the
brief, answers, recorded decisions, task definitions, and outputs. Use
`kw status <run>` to identify completed tasks awaiting verdicts.

Global verification is the default: one verifier examines the combined result
and records verdicts for all pending tasks in the same context. At a planned
checkpoint, assess the completed work needed to unblock implementation; do not
claim the unfinished overall result has passed.

## Assess the actual outcome

Check the combined deliverable against the original brief and recorded user
decisions, then attribute defects to the affected task criteria. Check sources
and substantive claims, not just whether files or table columns exist. Verify
the actual destination when the task includes publication.

Use mechanical checks for objective requirements where useful, and judgment
for relevance and factual support. Match the depth of checking to consequence.
On repair, check the findings and plausible regressions while preserving valid
work; finish with acceptance of the whole result.

Review inputs before their consumers and refresh `kw status` after each verdict.
A failure invalidates downstream outputs; those tasks must run again before
receiving verdicts.

For each pending task, write findings when needed:

```json
[{"key": "missing-source", "severity": "blocking", "text": "Candidates A and B lack the sources required by done_when."}]
```

- `blocking` means an agreed requirement is unmet. Do not downgrade a real
  failure or add new requirements.
- `note` is an improvement outside the acceptance criteria.
- Reuse the stable `key` when a defect persists, so repeated failures are visible.

Record each verdict with:

```sh
kw verdict <run> <id> pass
kw verdict <run> <id> fail --findings <file>
```

A pass can also include `--findings <file>` for notes. Record evidence against
the current output, not a superseded attempt.

When every pending task has a verdict, run `kw finish <run>`. It returns focused
failures for bounded repair, continues implementation after checkpoints, or
ends the run as `done` or `partial`. Check the returned state. The final report
must truthfully identify outputs, verification results, and unresolved work;
add useful substantive synthesis without changing that status.
