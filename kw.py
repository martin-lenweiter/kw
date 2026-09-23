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
import sys
import time
from pathlib import Path

LEDGER = "ledger.jsonl"
STATE = "state.json"
LOCK = ".lock"

DEFAULTS = {"max_repairs": 2, "max_rounds": 3, "lease_seconds": 1800}

# Run phases and the phases each one may move to.
PHASES = {
    "clarifying": {"awaiting-answers", "planning"},
    "awaiting-answers": {"clarifying", "planning"},
    "planning": {"awaiting-approval", "clarifying"},
    "awaiting-approval": {"planning", "executing"},
    "executing": {"verifying"},
    "verifying": {"executing", "done", "partial"},
    "done": set(),
    "partial": set(),
}
TASK_STATUSES = {"todo", "running", "done", "verified", "needs-human"}


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
        task.update(status="running", owner=ev["owner"], lease_until=ev["lease_until"])
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
    elif t == "release":
        for tid in ev["ids"]:
            tasks[tid].update(status="todo", owner=None, lease_until=None)
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
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    # A torn final line from a crash mid-append is ignored.
                    break
        return out

    def rebuild(self):
        state = empty_state()
        for ev in self.events():
            apply(state, ev)
        return state

    def load(self):
        try:
            state = json.loads(self.snapshot.read_text())
            if state.get("events") == len(self.events()):
                return state
        except (OSError, json.JSONDecodeError):
            pass
        return self.rebuild()

    def append(self, state, ev):
        ev = {"ts": round(now(), 3), **ev}
        line = json.dumps(ev, ensure_ascii=False) + "\n"
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


def next_action(state):
    """One-line instruction for whichever agent reads the run next."""
    p = state["phase"]
    c = counts(state)
    if p == "clarifying":
        return "planner: read brief.md; write questions to questions.md and move to awaiting-answers, or move to planning"
    if p == "awaiting-answers":
        return "human: answer questions.md, then planner moves to clarifying or planning"
    if p == "planning":
        return "planner: write plan.md and tasks.json, run the critic, kw tasks set, then move to awaiting-approval"
    if p == "awaiting-approval":
        return "human: review plan.md and critique.md, then kw approve (or move back to planning)"
    if p == "executing":
        if c["todo"] or c["running"]:
            return f"orchestrator: {c['todo']} todo, {c['running']} running; claim and execute tasks"
        return "orchestrator: all tasks executed; move to verifying"
    if p == "verifying":
        if c["done"]:
            return f"verifier: {c['done']} task(s) awaiting verification"
        return "verifier: kw finish (moves to executing for repairs, or done/partial)"
    return f"run is {p}"


def expire_leases(run, state):
    stale = [t["id"] for t in state["tasks"].values()
             if t["status"] == "running" and (t["lease_until"] or 0) < now()]
    if stale:
        run.append(state, {"type": "release", "ids": stale, "reason": "lease expired"})
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
            {k: state["tasks"][tid].get(k) for k in ("id", "status", "attempts", "owner", "stop_reason")}
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
        if to == "verifying" and any(t["status"] in ("todo", "running") for t in state["tasks"].values()):
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
        seen.add(s["id"])
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
        for tid in candidates:
            task = get_task(state, tid)
            if task["status"] == "todo":
                lease = args.lease or state["config"]["lease_seconds"]
                run.append(state, {"type": "claim", "id": tid, "owner": args.owner,
                                   "lease_until": now() + lease})
                return {"claimed": tid, "goal": task["goal"], "done_when": task["done_when"],
                        "attempts": task["attempts"], "last_findings": task["last_findings"]}
        return {"claimed": None}


def cmd_done(args):
    run = Run(args.run)
    with run.locked():
        state = run.load()
        require_phase(state, "executing")
        task = get_task(state, args.id)
        if task["status"] != "running":
            raise KwError(f"task {args.id} is {task['status']}, not running")
        if args.output and not (run.dir / args.output).exists():
            raise KwError(f"output not found: {args.output}")
        run.append(state, {"type": "done", "id": args.id, "output": args.output})
    return {"id": args.id, "status": "done"}


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
        if c["todo"]:
            if state["round"] >= state["config"]["max_rounds"]:
                ids = [t["id"] for t in state["tasks"].values() if t["status"] == "todo"]
                run.append(state, {"type": "escalate", "ids": ids,
                                   "reason": f"run round cap reached ({state['config']['max_rounds']})"})
            else:
                run.append(state, {"type": "phase", "from": "verifying", "to": "executing", "round_up": True})
                return {"phase": "executing", "round": state["round"], "repairs": c["todo"]}
        final = "done" if counts(state)["needs-human"] == 0 else "partial"
        run.append(state, {"type": "phase", "from": "verifying", "to": final})
    return {"phase": final, "counts": counts(state)}


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
    return {"phase": state["phase"], "released": released, "next": next_action(state)}


def cmd_rebuild(args):
    run = Run(args.run)
    with run.locked():
        state = run.rebuild()
        run.write_snapshot(state)
    return {"phase": state["phase"], "events": state["events"]}


def main(argv=None):
    p = argparse.ArgumentParser(prog="kw", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a run directory")
    s.add_argument("run")
    s.add_argument("--brief")
    s.add_argument("--title")
    for k in DEFAULTS:
        s.add_argument(f"--{k.replace('_', '-')}", dest=k, type=int)
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
    s.set_defaults(fn=cmd_done)

    s = sub.add_parser("verdict", help="record the verifier's verdict for a task")
    s.add_argument("run")
    s.add_argument("id")
    s.add_argument("verdict", choices=["pass", "fail"])
    s.add_argument("--findings", help="JSON list of {key, severity, text}")
    s.set_defaults(fn=cmd_verdict)

    s = sub.add_parser("finish", help="close a verify round")
    s.add_argument("run")
    s.set_defaults(fn=cmd_finish)

    s = sub.add_parser("resume", help="rebuild state and release expired leases")
    s.add_argument("run")
    s.add_argument("--force", action="store_true", help="release all running tasks")
    s.set_defaults(fn=cmd_resume)

    s = sub.add_parser("rebuild", help="rebuild state.json from the ledger")
    s.add_argument("run")
    s.set_defaults(fn=cmd_rebuild)

    args = p.parse_args(argv)
    try:
        result = args.fn(args)
    except KwError as e:
        print(json.dumps({"error": str(e)}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
