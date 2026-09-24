#!/usr/bin/env python3
"""kw: run state for plan -> execute -> verify agent workflows.

All state lives in one run directory. ledger.jsonl is the append-only source
of truth; state.json is a snapshot rebuilt from it on demand. Every command
takes an exclusive lock, validates the transition, appends one event, and
rewrites the snapshot atomically. Agents must change state only through kw.
"""

import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import uuid
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LEDGER = "ledger.jsonl"
STATE = "state.json"
LOCK = ".lock"

DEFAULTS = {"max_repairs": 2, "max_rounds": 3, "lease_seconds": 1800, "max_parallel": 0}
MODEL_TIERS = {"fast", "standard", "strong"}

# Run phases and the phases each one may move to.
PHASES = {
    "clarifying": {"awaiting-answers", "planning"},
    "awaiting-answers": {"clarifying", "planning"},
    "planning": {"awaiting-approval", "clarifying"},
    "awaiting-approval": {"planning", "executing"},
    "executing": {"verifying"},
    "verifying": {"executing", "done", "partial"},
    "done": set(),
    "partial": {"verifying"},  # only through kw resolve
}
TASK_STATUSES = {"todo", "running", "done", "verified", "needs-human"}
TERMINAL = {"verified", "needs-human"}


class KwError(Exception):
    pass


def now():
    return time.time()


# ---------------------------------------------------------------- state model

def empty_state():
    return {
        "phase": None,
        "round": 1,
        "config": dict(DEFAULTS),
        "plan_approved": False,
        "tasks": {},
        "order": [],
        "events": 0,
    }


def apply(state, ev):
    """Pure state transition. Validation happens before events are written."""
    t = ev["type"]
    tasks = state["tasks"]
    if t == "init":
        state["phase"] = "clarifying"
        state["config"].update(ev.get("config", {}))
        state["config"].setdefault("resources", {})
        state["title"] = ev.get("title", "")
    elif t == "phase":
        state["phase"] = ev["to"]
        if ev.get("round_up"):
            state["round"] += 1
    elif t == "tasks-set":
        tasks.clear()
        state["order"] = []
        for spec in ev["tasks"]:
            tasks[spec["id"]] = {
                "id": spec["id"],
                "goal": spec["goal"],
                "done_when": spec["done_when"],
                "depends_on": spec.get("depends_on", []),
                "acceptance": bool(spec.get("acceptance")),
                "uses": spec.get("uses", []),
                "model": spec.get("model", "standard" if state["config"].get("verification") != "global" else None),
                "effort": spec.get("effort"),
                "inputs": spec.get("inputs", []),
                "checkpoint": bool(spec.get("checkpoint")),
                "allow_partial_inputs": bool(spec.get("allow_partial_inputs", state["config"].get("verification") != "global")),
                "status": "todo",
                "attempts": 0,
                "owner": None,
                "lease_until": None,
                "output": None,
                "last_findings": [],
                "history": [],
            }
            state["order"].append(spec["id"])
    elif t == "approve":
        state["plan_approved"] = True
        state["phase"] = "executing"
    elif t == "claim":
        task = tasks[ev["id"]]
        task.update(status="running", owner=ev["owner"], lease_until=ev["lease_until"],
                    token=ev.get("token"), output_dir=ev.get("output_dir"))
    elif t == "done":
        task = tasks[ev["id"]]
        task.update(status="done", output=ev.get("output"), owner=None, lease_until=None)
    elif t == "pass":
        task = tasks[ev["id"]]
        task["status"] = "verified"
        task["notes"] = ev.get("notes", [])
    elif t == "fail":
        task = tasks[ev["id"]]
        task["attempts"] += 1
        task["history"].append({"round": state["round"], "findings": ev["findings"]})
        task["last_findings"] = ev["findings"]
        task["status"] = ev["next_status"]
        task["stop_reason"] = ev.get("stop_reason")
        invalidate_descendants(state, ev["id"])
    elif t == "release":
        for tid in ev["ids"]:
            tasks[tid].update(status="todo", owner=None, lease_until=None)
    elif t == "resolve":
        task = tasks[ev["id"]]
        invalidate_descendants(state, ev["id"], recover=True)
        task.update(status="todo" if ev.get("retry") else "done", stop_reason=None, resolution=ev["note"])
        if ev.get("retry"):
            task.update(output=None, token=None, owner=None, lease_until=None)
        if ev.get("output"):
            task["output"] = ev["output"]
        state["phase"] = "executing" if ev.get("retry") else "verifying"
    elif t == "escalate":
        for tid in ev["ids"]:
            tasks[tid]["status"] = "needs-human"
            tasks[tid]["stop_reason"] = ev["reason"]
    else:
        raise KwError(f"unknown event type {t}")
    state["events"] += 1
    return state


# ------------------------------------------------------------------- storage

class Run:
    def __init__(self, path):
        self.dir = Path(path)
        self.ledger = self.dir / LEDGER
        self.snapshot = self.dir / STATE

    @contextlib.contextmanager
    def locked(self):
        if not self.dir.is_dir():
            raise KwError(f"no run directory: {self.dir}")
        with open(self.dir / LOCK, "a") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def events(self):
        if not self.ledger.exists():
            return []
        out = []
        with open(self.ledger) as fh:
            for number, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                    if not isinstance(event, dict) or "type" not in event:
                        raise KwError(f"invalid ledger record at line {number}")
                    out.append(event)
                except json.JSONDecodeError as exc:
                    raise KwError(f"corrupt ledger at line {number}: {exc.msg}") from exc
        return out

    def rebuild(self):
        state = empty_state()
        for ev in self.events():
            try:
                apply(state, ev)
            except (KeyError, TypeError, ValueError) as exc:
                raise KwError(f"invalid ledger event {state['events'] + 1}: {exc}") from exc
        return state

    def load(self):
        # The ledger is authoritative; rebuilding also validates every record.
        return self.rebuild()

    def append(self, state, ev):
        ev = {"ts": round(now(), 3), **ev}
        line = json.dumps(ev, ensure_ascii=False) + "\n"
        if self.ledger.exists() and self.ledger.stat().st_size:
            with open(self.ledger, "rb") as tail:
                tail.seek(-1, os.SEEK_END)
                if tail.read(1) != b"\n":
                    line = "\n" + line
        with open(self.ledger, "a") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
        apply(state, ev)
        self.write_snapshot(state)
        return state

    def write_snapshot(self, state):
        tmp = self.snapshot.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        os.replace(tmp, self.snapshot)


# ------------------------------------------------------------------ commands

def require_phase(state, *phases):
    if state["phase"] not in phases:
        raise KwError(f"phase is {state['phase']}; expected one of {', '.join(phases)}")


def get_task(state, tid):
    if tid not in state["tasks"]:
        raise KwError(f"unknown task {tid}")
    return state["tasks"][tid]


def counts(state):
    c = {s: 0 for s in sorted(TASK_STATUSES)}
    for t in state["tasks"].values():
        c[t["status"]] += 1
    return c


def invalidate_descendants(state, tid, recover=False):
    affected = {tid}
    changed = True
    while changed:
        changed = False
        for task in state["tasks"].values():
            if task["id"] not in affected and affected.intersection(task["depends_on"]):
                affected.add(task["id"])
                changed = True
                if task["status"] in ("done", "verified") or (
                    recover and task["status"] == "needs-human"
                    and task.get("stop_reason", "").startswith("blocked dependency")
                ):
                    task.update(status="todo", output=None, stop_reason=None)


def ready(state, task):
    for dep in task.get("depends_on", []):
        source = state["tasks"][dep]
        status = source["status"]
        if status == "verified":
            continue
        if status == "needs-human" and task.get("allow_partial_inputs"):
            continue
        if (status == "done" and state["config"].get("verification") == "global"
                and not source.get("checkpoint")):
            continue
        return False
    return True


def settle_blocked(run, state):
    while True:
        ids = [t["id"] for t in state["tasks"].values() if t["status"] == "todo"
               and not t.get("allow_partial_inputs")
               and any(state["tasks"][d]["status"] == "needs-human" for d in t["depends_on"])]
        if not ids:
            return
        run.append(state, {"type": "escalate", "ids": ids, "reason": "blocked dependency needs resolution"})


def write_report(run, state):
    lines = [f"# {state.get('title', 'KW run')}", "", f"Status: {state['phase']}", ""]
    for tid in state["order"]:
        task = state["tasks"][tid]
        lines.append(f"- {tid}: {task['status']} — {task['goal']}")
        if task.get("output"):
            lines.append(f"  Output: [{task['output']}]({task['output']})")
        if task["status"] == "verified":
            for note in task.get("notes", []):
                lines.append(f"  Verification note: {note['text']}")
        if task.get("stop_reason"):
            lines.append(f"  Unresolved: {task['stop_reason']}")
        for finding in task.get("last_findings", []) if task["status"] != "verified" else []:
            lines.append(f"  Finding: {finding['text']}")
    tmp = run.dir / "report.md.tmp"
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, run.dir / "report.md")


def blocked_todo(state):
    return [t["id"] for t in state["tasks"].values() if t["status"] == "todo" and not ready(state, t)]


def capacity_block(state, task):
    """Name of a resource or limit that stops this task from starting now, else None.

    Resources are declared per run with a capacity (how many running tasks may
    use them at once). Read-only surfaces get high capacity; paid calls and
    writes get 1. Undeclared resources are unlimited.
    """
    running = [t for t in state["tasks"].values() if t["status"] == "running"]
    limit = state["config"].get("max_parallel") or 0
    if limit and len(running) >= limit:
        return f"max_parallel {limit}"
    caps = state["config"].get("resources", {})
    for r in task.get("uses", []):
        if r in caps and sum(r in t.get("uses", []) for t in running) >= caps[r]:
            return f"resource {r} at capacity {caps[r]}"
    return None


def next_action(state):
    """One-line instruction for whichever agent reads the run next."""
    p = state["phase"]
    c = counts(state)
    if p == "clarifying":
        return "planner: read brief.md; write questions to questions.md and move to awaiting-answers, or move to planning"
    if p == "awaiting-answers":
        return "human: answer questions.md, then planner moves to clarifying or planning"
    if p == "planning":
        return "planner: write plan.md and tasks.json, kw tasks set, then move to awaiting-approval"
    if p == "awaiting-approval":
        return "human: review plan.md, then kw approve (or move back to planning)"
    if p == "executing":
        blocked = len(blocked_todo(state))
        if c["todo"] - blocked or c["running"]:
            return f"orchestrator: {c['todo']} todo, {c['running']} running; claim and execute tasks"
        return "orchestrator: all runnable tasks executed; move to verifying" + (
            f" ({blocked} task(s) wait for dependencies)" if blocked else "")
    if p == "verifying":
        if c["done"]:
            return f"verifier: {c['done']} task(s) awaiting verification"
        return "verifier: kw finish (moves to executing for repairs, or done/partial)"
    return f"run is {p}"


def expire_leases(run, state):
    stale = [t["id"] for t in state["tasks"].values()
             if t["status"] == "running" and (t["lease_until"] or 0) < now()]
    if stale:
        run.append(state, {"type": "escalate", "ids": stale, "reason": "lease expired; stop the old worker and inspect external writes before resolving"})
    return stale


def cmd_init(args):
    d = Path(args.run)
    if (d / LEDGER).exists():
        raise KwError(f"run already exists: {d}")
    d.mkdir(parents=True, exist_ok=True)
    (d / "out").mkdir(exist_ok=True)
    if args.brief:
        (d / "brief.md").write_text(Path(args.brief).read_text())
    elif not (d / "brief.md").exists():
        (d / "brief.md").write_text("# Brief\n\nGoal:\n\nInputs:\n\nOutput:\n\nAcceptance criteria:\n")
    config = {k: getattr(args, k) for k in DEFAULTS if getattr(args, k) is not None}
    resources = {}
    for item in args.resource or []:
        name, _, cap = item.partition("=")
        if not name or not cap.isdigit() or int(cap) < 1:
            raise KwError(f"--resource needs name=capacity (capacity >= 1): {item}")
        resources[name] = int(cap)
    config["resources"] = resources
    config["verification"] = args.verification
    run = Run(d)
    with run.locked():
        run.append(empty_state(), {"type": "init", "title": args.title or d.name, "config": config})
    return {"run": str(d), "phase": "clarifying"}


def cmd_status(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
    return {
        "phase": state["phase"],
        "round": state["round"],
        "plan_approved": state["plan_approved"],
        "counts": counts(state),
        "next": next_action(state),
        "tasks": [
            {k: state["tasks"][tid].get(k) for k in ("id", "status", "attempts", "owner", "token", "output_dir", "output", "model", "effort", "stop_reason")}
            for tid in state["order"]
        ],
    }


def cmd_phase(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        cur, to = state["phase"], args.to
        if to not in PHASES.get(cur, set()):
            raise KwError(f"cannot move {cur} -> {to}")
        if to == "executing" and cur == "awaiting-approval":
            raise KwError("use kw approve to start execution")
        if to == "verifying":
            waiting = set(blocked_todo(state))
            if any(t["status"] == "running" or (t["status"] == "todo" and t["id"] not in waiting)
                   for t in state["tasks"].values()):
                raise KwError("tasks still todo or running")
        if to == "awaiting-approval" and not state["tasks"]:
            raise KwError("no tasks set")
        if cur == "verifying":
            raise KwError("use kw finish to leave verifying")
        run.append(state, {"type": "phase", "from": cur, "to": to})
    return {"phase": to}


def cmd_tasks_set(args):
    specs = json.loads(Path(args.file).read_text())
    if isinstance(specs, dict):
        specs = specs.get("tasks", [])
    seen = set()
    for s in specs:
        for key in ("id", "goal", "done_when"):
            if not s.get(key):
                raise KwError(f"task missing {key}: {s}")
        if s["id"] in seen:
            raise KwError(f"duplicate task id {s['id']}")
        if not isinstance(s["id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", s["id"]):
            raise KwError("task id must contain only letters, numbers, underscores and hyphens")
        for field in ("model", "effort"):
            if s.get(field) is not None and (not isinstance(s[field], str) or not s[field].strip()):
                raise KwError(f"task {s['id']} {field} must be a nonempty string")
        seen.add(s["id"])
    for s in specs:
        for d in s.get("depends_on", []):
            if d not in seen or d == s["id"]:
                raise KwError(f"task {s['id']} has invalid dependency {d}")
    deps = {s["id"]: s.get("depends_on", []) for s in specs}
    visiting, done = set(), set()

    def visit(tid):
        if tid in done:
            return
        if tid in visiting:
            raise KwError(f"dependency cycle at {tid}")
        visiting.add(tid)
        for d in deps[tid]:
            visit(d)
        visiting.discard(tid)
        done.add(tid)

    for tid in deps:
        visit(tid)

    # Exactly one acceptance task checks the whole result against the brief, so
    # it must (transitively) depend on every other task.
    accept = [s["id"] for s in specs if s.get("acceptance")]
    if len(accept) != 1:
        raise KwError("plan needs exactly one task with \"acceptance\": true")

    def upstream(tid, acc):
        for d in deps[tid]:
            if d not in acc:
                acc.add(d)
                upstream(d, acc)
        return acc

    missing = set(deps) - {accept[0]} - upstream(accept[0], set())
    if missing:
        raise KwError(f"acceptance task {accept[0]} must depend on: {', '.join(sorted(missing))}")
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "planning")
        if state["plan_approved"]:
            raise KwError("plan is approved and frozen")
        run.append(state, {"type": "tasks-set", "tasks": specs})
    return {"tasks": len(specs)}


def cmd_approve(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "awaiting-approval")
        run.append(state, {"type": "approve", "by": args.by})
    return {"phase": "executing", "tasks": len(state["tasks"])}


def cmd_claim(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "executing")
        expire_leases(run, state)
        candidates = [args.id] if args.id else state["order"]
        held = {}
        for tid in candidates:
            task = get_task(state, tid)
            if task["status"] == "todo" and ready(state, task):
                block = capacity_block(state, task)
                if block:
                    held[tid] = block
                    continue
                lease = args.lease or state["config"]["lease_seconds"]
                if lease <= 0:
                    raise KwError("lease must be positive")
                token = uuid.uuid4().hex
                output_dir = f"out/{tid}/{token}"
                (run.dir / output_dir).mkdir(parents=True, exist_ok=True)
                run.append(state, {"type": "claim", "id": tid, "owner": args.owner,
                                   "lease_until": now() + lease, "token": token, "output_dir": output_dir})
                return {"claimed": tid, "goal": task["goal"], "done_when": task["done_when"],
                        "model": task.get("model"), "effort": task.get("effort"),
                        "token": token, "output_dir": output_dir, "inputs": task.get("inputs", []),
                        "uses": task.get("uses", []),
                        "attempts": task["attempts"], "last_findings": task["last_findings"],
                        "dependencies": {d: {"status": state["tasks"][d]["status"],
                                             "output": state["tasks"][d]["output"]}
                                         for d in task["depends_on"]}}
        return {"claimed": None, "waiting_on_dependencies": blocked_todo(state),
                "waiting_on_capacity": held}


def cmd_done(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "executing")
        task = get_task(state, args.id)
        if task["status"] != "running":
            raise KwError(f"task {args.id} is {task['status']}, not running")
        if task.get("token"):
            if args.token != task["token"] or task["lease_until"] <= now():
                raise KwError("claim token does not match or lease expired")
            if not args.output:
                raise KwError("output required")
            target = (run.dir / args.output).resolve()
            if not target.is_relative_to((run.dir / task["output_dir"]).resolve()):
                raise KwError("output must be within the current attempt output_dir")
        if args.output and not (run.dir / args.output).is_file():
            raise KwError(f"output not found: {args.output}")
        run.append(state, {"type": "done", "id": args.id, "output": args.output})
    return {"id": args.id, "status": "done"}


def cmd_block(args):
    """Record a worker blocker without pretending implementation completed."""
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "executing")
        task = get_task(state, args.id)
        if task["status"] != "running" or task.get("token") != args.token:
            raise KwError("block requires the active claim token")
        run.append(state, {"type": "escalate", "ids": [args.id], "reason": args.reason})
    return {"id": args.id, "status": "needs-human"}


def read_findings(path):
    findings = json.loads(Path(path).read_text())
    if isinstance(findings, dict):
        findings = findings.get("findings", [])
    for f in findings:
        if f.get("severity") not in ("blocking", "note") or not f.get("key") or not f.get("text"):
            raise KwError(f"finding needs key, severity (blocking|note) and text: {f}")
    return findings


def cmd_verdict(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "verifying")
        task = get_task(state, args.id)
        if task["status"] != "done":
            raise KwError(f"task {args.id} is {task['status']}, not done")
        findings = read_findings(args.findings) if args.findings else []
        blocking = [f for f in findings if f["severity"] == "blocking"]
        if args.verdict == "pass":
            if blocking:
                raise KwError("pass given with blocking findings")
            run.append(state, {"type": "pass", "id": args.id, "notes": findings})
            return {"id": args.id, "status": "verified"}
        if not blocking:
            raise KwError("fail needs at least one blocking finding")
        cfg = state["config"]
        previous = {f["key"] for f in task["last_findings"] if f["severity"] == "blocking"}
        repeated = sorted(previous & {f["key"] for f in blocking})
        if repeated:
            status, reason = "needs-human", f"stalled: repeated finding(s) {', '.join(repeated)}"
        elif task["attempts"] + 1 > cfg["max_repairs"]:
            status, reason = "needs-human", f"repair cap reached ({cfg['max_repairs']})"
        else:
            status, reason = "todo", None
        run.append(state, {"type": "fail", "id": args.id, "findings": findings,
                           "next_status": status, "stop_reason": reason})
    return {"id": args.id, "status": status, "stop_reason": reason}


def cmd_finish(args):
    """Close a verify round: loop back for repairs, or end the run."""
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "verifying")
        c = counts(state)
        if c["done"]:
            raise KwError(f"{c['done']} task(s) still await a verdict")
        if c["running"]:
            raise KwError("tasks still running")
        settle_blocked(run, state)
        if counts(state)["todo"]:
            repairs = [t["id"] for t in state["tasks"].values() if t["status"] == "todo" and t["attempts"]]
            # The run round cap stops tasks that already had a repair. A task that
            # failed for the first time still gets one repair; stall detection and
            # max_repairs bound it.
            capped = [tid for tid in repairs if state["tasks"][tid]["attempts"] >= 2]
            if capped and state["round"] >= state["config"]["max_rounds"]:
                run.append(state, {"type": "escalate", "ids": capped,
                                   "reason": f"run round cap reached ({state['config']['max_rounds']})"})
            settle_blocked(run, state)
            if counts(state)["todo"]:
                # Only repair rounds count against max_rounds; dependency stages do not.
                run.append(state, {"type": "phase", "from": "verifying", "to": "executing",
                                   "round_up": bool(repairs)})
                return {"phase": "executing", "round": state["round"],
                        "repairs": [r for r in repairs if state["tasks"][r]["status"] == "todo"],
                        "ready": [t["id"] for t in state["tasks"].values()
                                  if t["status"] == "todo" and ready(state, t)]}
        accept = [t for t in state["tasks"].values() if t.get("acceptance")]
        accepted = all(t["status"] == "verified" for t in accept)
        final = "done" if counts(state)["needs-human"] == 0 and accepted else "partial"
        run.append(state, {"type": "phase", "from": "verifying", "to": final})
        write_report(run, state)
    return {"phase": final, "counts": counts(state)}


def cmd_resolve(args):
    """A human fixed a needs-human task; send it back through verification."""
    run = Run(args.run)
    with run.locked():
        state = run.load()
        task = get_task(state, args.id)
        if task["status"] != "needs-human":
            raise KwError(f"task {args.id} is {task['status']}, not needs-human")
        if state["phase"] not in ("executing", "verifying", "partial"):
            raise KwError(f"cannot resolve in phase {state['phase']}")
        if any(t["status"] == "running" for t in state["tasks"].values()):
            raise KwError("tasks still running")
        if args.output and not (run.dir / args.output).is_file():
            raise KwError(f"output not found: {args.output}")
        if args.retry and args.output:
            raise KwError("--retry cannot be combined with --output")
        if not args.retry and not (args.output or task.get("output")):
            raise KwError("provide repaired --output or use --retry after stopping the old worker")
        run.append(state, {"type": "resolve", "id": args.id, "note": args.note,
                           "by": args.by, "output": args.output, "retry": args.retry})
    return {"id": args.id, "status": task["status"], "phase": state["phase"]}


def cmd_resume(args):
    run = Run(args.run)
    with run.locked():
        state = run.rebuild()
        run.write_snapshot(state)
        released = []
        if state["phase"] == "executing":
            if args.force:
                released = [t["id"] for t in state["tasks"].values() if t["status"] == "running"]
                if released:
                    run.append(state, {"type": "release", "ids": released, "reason": "resume --force"})
            else:
                released = expire_leases(run, state)
        if state["phase"] in ("done", "partial"):
            write_report(run, state)
    return {"phase": state["phase"], "released": released, "next": next_action(state)}


def cmd_rebuild(args):
    run = Run(args.run)
    with run.locked():
        state = run.rebuild()
        run.write_snapshot(state)
    return {"phase": state["phase"], "events": state["events"]}


# ---------------------------------------------------------------------- loop
# kw loop drives a run headlessly through any CLI agent harness. It never
# decides for the human: it stops at awaiting-answers and awaiting-approval.

SKILLS = Path(__file__).resolve().parent / "skills"
HARNESSES = {
    "claude": {
        "cmd": ["claude", "-p", "{prompt}", "--permission-mode", "acceptEdits",
                "--allowedTools=Bash,Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"],
        "native": "claude",
        "models": {"fast": "haiku", "standard": "sonnet", "strong": "opus"},
    },
    "codex": {
        "cmd": ["codex", "exec", "--skip-git-repo-check", "--sandbox", "workspace-write", "{prompt}"],
        "native": "codex",
        "models": {"fast": "low", "standard": "medium", "strong": "high"},
    },
}


def loop_config(run_dir, harness):
    """Harness template, overridable per run in <run>/kw-loop.json."""
    cfg = json.loads(json.dumps(HARNESSES[harness])) if harness in HARNESSES else {"cmd": [], "models": {}}
    path = Path(run_dir) / "kw-loop.json"
    if path.exists():
        override = json.loads(path.read_text()).get(harness, {})
        cfg.update(override)
        if "cmd" in override:
            cfg.pop("native", None)
    if not cfg.get("cmd"):
        raise KwError(f"no command template for harness {harness}")
    return cfg


def agent_call(cfg, run_dir, name, model_tier, prompt, timeout, effort=None):
    model = cfg.get("models", {}).get(model_tier, model_tier) or ""
    cmd = [part.replace("{model}", model).replace("{effort}", effort or "")
           .replace("{prompt}", prompt).replace("{run}", str(run_dir)) for part in cfg["cmd"]]
    native = cfg.get("native")
    if native == "codex":
        if model_tier in MODEL_TIERS:
            effort = effort or model
        elif model:
            cmd[2:2] = ["--model", model]
        if effort:
            cmd[2:2] = ["-c", f'model_reasoning_effort="{effort}"']
    elif native == "claude":
        if model:
            cmd.extend(["--model", model])
        if effort:
            cmd.extend(["--effort", effort])
    logs = Path(run_dir) / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / f"{name}-{uuid.uuid4().hex}.log"
    with open(log, "w") as fh:
        fh.write("$ " + shlex.join([c if c != prompt else "<prompt>" for c in cmd]) + "\n\n")
        fh.flush()
        try:
            with subprocess.Popen(cmd, cwd=run_dir, stdin=subprocess.DEVNULL, stdout=fh,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True) as process:
                try:
                    code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
                    code = 124
        except OSError as exc:
            fh.write(str(exc))
            code = 127
    output = None
    for line in reversed(log.read_text().splitlines()):
        if line.strip().startswith("KW_OUTPUT:"):
            output = line.split(":", 1)[1].strip()
            break
    return code, output, log


def status_of(run_dir):
    run = Run(run_dir)
    with run.locked():
        return run.load()


def plan_prompt(run_dir, phase):
    return (f"You are the kw planner for the run in {run_dir}. Follow {SKILLS}/kw-plan/SKILL.md exactly. "
            f"The run is in phase {phase}. Use the kw CLI (on PATH as kw) for every state change. "
            "Stop as soon as the run reaches awaiting-answers or awaiting-approval; never approve the plan yourself.")


def work_prompt(run_dir, task):
    repair = ""
    if task.get("attempts"):
        repair = ("This is a repair. Fix every blocking finding below and do not redo passing work. "
                  f"Findings: {json.dumps(task.get('last_findings', []), ensure_ascii=False)} ")
    deps = task.get("dependencies") or {}
    return (f"You are a kw worker for task {task['claimed']} in the run at {run_dir}. "
            f"Follow {SKILLS}/kw-run/SKILL.md for the worker rules. Read brief.md and decisions.md if present; "
            f"consult plan.md and supporting context as needed. Inputs: {json.dumps(task.get('inputs', []))}. Task goal: {task['goal']} "
            f"done_when: {task['done_when']} Dependency outputs: {json.dumps(deps, ensure_ascii=False)} {repair}"
            f"Write your result inside {task['output_dir']}/ (write a temp file, then rename). "
            "Before repeating external publishing, check saved destination IDs and whether the write succeeded. "
            "Do not run kw. End your reply with one line: KW_OUTPUT: <path relative to the run dir>.")


def verify_prompt(run_dir, pending):
    return (f"You are the independent kw verifier for the run at {run_dir}. "
            f"Follow {SKILLS}/kw-verify/SKILL.md. Assess the combined result against brief.md and decisions.md. "
            f"Pending tasks: {json.dumps(pending)}. Record verdicts with kw verdict {run_dir} <id> pass|fail "
            "--findings <file>. Review dependencies before their consumers. Refresh state after each verdict: "
            "a failure invalidates downstream outputs, which must not receive verdicts until rerun. "
            "At an intermediate checkpoint, assess the available work without claiming final acceptance. "
            "Do not run kw finish.")


def block_failed_attempt(run_dir, task, reason):
    run = Run(run_dir)
    with run.locked():
        state = run.load()
        current = get_task(state, task["claimed"])
        if current["status"] == "running" and current.get("token") == task["token"]:
            run.append(state, {"type": "escalate", "ids": [task["claimed"]], "reason": reason})


def cmd_loop(args):
    run_dir = Path(args.run).resolve()
    cfg = loop_config(run_dir, args.harness)
    if args.timeout <= 0:
        raise KwError("timeout must be positive")
    log = []
    for _ in range(args.max_steps):
        state = status_of(run_dir)
        phase = state["phase"]
        if phase in ("awaiting-answers", "awaiting-approval", "done", "partial"):
            if phase in ("done", "partial"):
                result = run_json(["resume", str(run_dir)])
                if "error" in result:
                    return {"stopped": phase, **result, "steps": log}
            return {"stopped": phase, "next": next_action(state), "steps": log}
        if phase in ("clarifying", "planning"):
            code, _, lg = agent_call(cfg, run_dir, f"plan-{phase}", args.model, plan_prompt(run_dir, phase), args.timeout, args.effort)
            log.append({"role": "planner", "phase": phase, "exit": code, "log": str(lg)})
            if code != 0 or status_of(run_dir)["phase"] == phase:
                return {"stopped": phase, "error": "planner made no progress", "steps": log}
            continue
        if phase == "executing":
            result = run_json(["resume", str(run_dir)])
            if "error" in result:
                return {"stopped": phase, **result, "steps": log}
            claims = []
            while True:
                r = run_json(["claim", str(run_dir), "--owner", f"kw-loop-{args.harness}",
                              "--lease", str(args.timeout + 60)])
                if "error" in r:
                    return {"stopped": phase, **r, "steps": log}
                if not r.get("claimed"):
                    break
                claims.append(r)
            if not claims:
                if any(t["status"] == "running" for t in status_of(run_dir)["tasks"].values()):
                    return {"stopped": phase, "error": "tasks running under another owner", "steps": log}
                r = run_json(["phase", str(run_dir), "verifying"])
                log.append({"role": "orchestrator", "phase": "verifying", "result": r})
                if "error" in r:
                    return {"stopped": phase, **r, "steps": log}
                continue

            def work(task):
                code, output, lg = agent_call(cfg, run_dir, f"work-{task['claimed']}", task.get("model") or args.model,
                                              work_prompt(run_dir, task), args.timeout, task.get("effort") or args.effort)
                result = {"error": f"worker exit {code}; missing or invalid output"}
                if code == 0 and output:
                    result = run_json(["done", str(run_dir), task["claimed"], "--output", output,
                                       "--token", task["token"]])
                    if "error" not in result:
                        return {"task": task["claimed"], "done": output, "log": str(lg)}
                block_failed_attempt(run_dir, task, result["error"] + "; inspect output and external writes before resolving")
                return {"task": task["claimed"], "exit": code, "output": output, "log": str(lg), **result}

            with ThreadPoolExecutor(max_workers=len(claims)) as ex:
                log.extend(ex.map(work, claims))
            continue
        if phase == "verifying":
            pending = [t["id"] for t in state["tasks"].values() if t["status"] == "done"]
            if pending:
                code, _, lg = agent_call(cfg, run_dir, "verify", args.model,
                                         verify_prompt(run_dir, pending), args.timeout, args.effort)
                log.append({"role": "verifier", "tasks": pending, "exit": code, "log": str(lg)})
                left = [t["id"] for t in status_of(run_dir)["tasks"].values() if t["status"] == "done"]
                if code != 0 or left:
                    return {"stopped": phase, "error": f"verifier exit {code}; tasks without verdict: {left}", "steps": log}
            r = run_json(["finish", str(run_dir)])
            log.append({"role": "orchestrator", "finish": r})
            if "error" in r:
                return {"stopped": phase, **r, "steps": log}
            continue
        return {"stopped": phase, "steps": log}
    return {"stopped": "max-steps", "steps": log}


def run_json(argv):
    """Run a kw command in-process (thread-safe, no stdout) and return its result or error."""
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KwError as e:
        return {"error": str(e)}


def build_parser():
    p = argparse.ArgumentParser(prog="kw", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a run directory")
    s.add_argument("run")
    s.add_argument("--brief")
    s.add_argument("--title")
    for k in DEFAULTS:
        s.add_argument(f"--{k.replace('_', '-')}", dest=k, type=int)
    s.add_argument("--verification", choices=["global", "per-task"], default="global")
    s.add_argument("--resource", action="append",
                   help="name=capacity, e.g. web-search=8 chrome=4 clay=1 (repeatable)")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("status", help="phase, task counts and next action")
    s.add_argument("run")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("phase", help="move the run to another phase")
    s.add_argument("run")
    s.add_argument("to", choices=sorted(PHASES))
    s.set_defaults(fn=cmd_phase)

    s = sub.add_parser("tasks", help="set the task list from a JSON file (planning only)")
    s.add_argument("action", choices=["set"])
    s.add_argument("run")
    s.add_argument("file")
    s.set_defaults(fn=cmd_tasks_set)

    s = sub.add_parser("approve", help="approve and freeze the plan; start execution")
    s.add_argument("run")
    s.add_argument("--by", default="human")
    s.set_defaults(fn=cmd_approve)

    s = sub.add_parser("claim", help="claim the next todo task (or --id)")
    s.add_argument("run")
    s.add_argument("--owner", required=True)
    s.add_argument("--id")
    s.add_argument("--lease", type=int)
    s.set_defaults(fn=cmd_claim)

    s = sub.add_parser("done", help="mark a claimed task executed")
    s.add_argument("run")
    s.add_argument("id")
    s.add_argument("--output")
    s.add_argument("--token")
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("block", help="record why a claimed task cannot continue")
    s.add_argument("run")
    s.add_argument("id")
    s.add_argument("--token", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_block)

    s = sub.add_parser("verdict", help="record the verifier's verdict for a task")
    s.add_argument("run")
    s.add_argument("id")
    s.add_argument("verdict", choices=["pass", "fail"])
    s.add_argument("--findings", help="JSON list of {key, severity, text}")
    s.set_defaults(fn=cmd_verdict)

    s = sub.add_parser("finish", help="close a verify round")
    s.add_argument("run")
    s.set_defaults(fn=cmd_finish)

    s = sub.add_parser("resolve", help="record a human fix for a needs-human task and re-verify it")
    s.add_argument("run")
    s.add_argument("id")
    s.add_argument("--note", required=True)
    s.add_argument("--by", default="human")
    s.add_argument("--output")
    s.add_argument("--retry", action="store_true", help="retry after stopping the old worker and checking external writes")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("resume", help="rebuild state and flag expired claims for resolution")
    s.add_argument("run")
    s.add_argument("--force", action="store_true", help="release running tasks only after stopping old workers and reconciling external writes")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("rebuild", help="rebuild state.json from the ledger")
    s.add_argument("run")
    s.set_defaults(fn=cmd_rebuild)

    s = sub.add_parser("loop", help="drive the run headlessly through a CLI agent harness")
    s.add_argument("run")
    s.add_argument("--harness", default="claude", help="claude, codex, or a name defined in <run>/kw-loop.json")
    s.add_argument("--model", help="model for planner/verifier and workers without an override; native default if omitted")
    s.add_argument("--effort", help="reasoning effort, independent of model")
    s.add_argument("--timeout", type=int, default=3600, help="seconds per agent call")
    s.add_argument("--max-steps", type=int, default=50)
    s.set_defaults(fn=cmd_loop)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = args.fn(args)
    except KwError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
