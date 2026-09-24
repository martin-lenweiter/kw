---
name: kw-graph
description: Show a kw run's task graph and where the run is. Use when asked to draw, show, or visualize a kw run, its tasks, dependencies, or progress. Read-only.
---

# Graph

Run `kw graph <run>` and show its output verbatim in a `text` code block. It
does not change state.

If no run is named, use the run directory in the current conversation. If there
is none, find directories that contain `ledger.jsonl` under the working
directory, choose the one whose ledger changed most recently, and name it.

After the block, add at most two lines: what blocks progress (`[!]` tasks and
their reasons from `kw status <run>`) and the `Next:` action. Do not repeat the
graph in prose.
