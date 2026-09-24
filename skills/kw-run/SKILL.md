---
name: kw-run
description: Coordinate implementation of an approved kw plan in phase executing. Claim work, delegate coherent assignments, record outputs, and handle focused repairs.
---

# Implement

Use `kw` for all state changes. Start with `kw resume <run>` and
`kw status <run>`. Resume marks expired claims as needing attention; it does not stop old
processes or establish whether external actions succeeded.

## Coordinate work

1. `kw claim <run> --owner <agent-id>` returns an assignment, dependency outputs,
   attempt token, and output directory, or `claimed: null`.
2. Give the worker the assignment, essential scope and decisions, and pointers
   to relevant inputs. Let it inspect further context when useful. On repair,
   include prior findings and preserve passing work.
3. Write the attempt's result at `out/<id>/<token>/result.md`. Link supporting artifacts
   as needed; no universal row schema is required. Publish the file atomically.
4. Record completion with
   `kw done <run> <id> --token <token> --output out/<id>/<token>/result.md`.
   Check that the command succeeded before reporting completion.

Run independent assignments in parallel when useful and supported. Respect
resource limits and use a designated writer for shared external destinations.
Use the assigned model and separate effort setting, or runtime defaults; do not
substitute a model silently.

Completed ordinary tasks unlock their consumers. Tasks marked `checkpoint`
require verification first. When nothing is runnable and no workers remain,
use `kw phase <run> verifying` and hand over to an independent verifier. Do not
create a separate verifier for every task by default.

## Completion and recovery

- Finish the authorized outcome without asking permission again for ordinary
  methods or recoverable problems. Preserve the approved goal and constraints.
- Record material user decisions in `decisions.md` so workers and the verifier
  can see them. Do not silently change goals or completion criteria.
- Distinguish incomplete work from successful implementation. Report missing
  context or blocked access with concrete findings; never invent results.
  If the coordinator cannot resolve it, record `kw block <run> <id> --token
  <token> --reason "what is missing"` instead of completing the task.
- Distinguish process failures from quality repairs. Inspect the failure before
  retrying, respect retry limits, and do not repeat the same failing approach.
- Before repeating an external write, check the destination for success using
  its saved identifier. A timeout does not establish that publication failed.
- Respect quotas and permissions. Do not switch harnesses to bypass a limit.

For a `needs-human` task, surface its findings. After an authorized fix, record
it in `decisions.md` and use
`kw resolve <run> <id> --note "what was fixed"` to return it for review.

For a failed process that needs a new attempt, first stop the old worker and
reconcile any external write. Then use `kw resolve <run> <id> --retry --note
"reason"`. A repaired existing result can instead use `kw resolve <run> <id> --output <path> --note "fix"` for independent verification.
