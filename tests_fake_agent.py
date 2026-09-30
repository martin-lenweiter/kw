"""Fake CLI agent for testing the loop: acts as planner, worker or verifier."""
import json, os, re, subprocess, sys
from pathlib import Path

prompt = sys.argv[-1]
run = Path.cwd()
LO = [sys.executable, str(Path(__file__).with_name("lo.py"))]

def lo(*a):
    return subprocess.run(LO + list(a), capture_output=True, text=True, check=True)

if "lo planner" in prompt:
    phase = json.loads(lo("status", str(run)).stdout)["phase"]
    if phase == "clarifying":
        lo("phase", str(run), "planning")
    tasks = [{"id": "t1", "goal": "g1", "done_when": "file says ok", "model": "model-a"},
             {"id": "t2", "goal": "g2", "done_when": "file says ok",
              "checkpoint": "human" if os.environ.get("FAKE_HUMAN_CHECKPOINT") == "t2" else False},
             {"id": "acc", "goal": "check all", "done_when": "both ok", "depends_on": ["t1", "t2"],
              "acceptance": True, "model": "model-b"}]
    (run / "tasks.json").write_text(json.dumps(tasks))
    (run / "plan.md").write_text("plan")
    lo("tasks", "set", str(run), str(run / "tasks.json"))
    lo("phase", str(run), "awaiting-approval")
    print("planned")
elif "lo worker for task" in prompt:
    tid = re.search(r"lo worker for task (\S+)", prompt).group(1)
    state = json.loads((run / "state.json").read_text())
    out = run / state["tasks"][tid]["output_dir"] / "result.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "ok"
    if os.environ.get("FAKE_DECISIONS") and tid == "t1":
        body += "\n\n## Decisions\n\nUse UTC for all dates.\n"
    if os.environ.get("FAKE_DECISIONS") and tid == "t2":
        body += "\n\n## Needs decision\n\nWhich provider should we use?\n"
    out.write_text(body)
    (run / "out" / f"{tid}.calls").open("a").write(sys.argv[-2] + "\n")  # records model used
    print(f"TASK_OUTPUT: {out.relative_to(run)}")
elif "lo verifier" in prompt:
    (run / "out" / "verifier.calls").open("a").write("stage\n")
    # Refresh after each verdict: failing an input invalidates completed consumers.
    while True:
        state = json.loads((run / "state.json").read_text())
        listed = json.loads(re.search(r"Pending tasks: (\[.*?\])", prompt).group(1))
        pending = [tid for tid in listed if state["tasks"][tid]["status"] == "done"]
        if not pending:
            break
        tid = pending[0]
        marker = run / "out" / f"{tid}.failed-once"
        f = run / "verify" / f"{tid}.json"
        f.parent.mkdir(exist_ok=True)
        if tid == "t1" and not marker.exists():
            marker.write_text("x")
            f.write_text(json.dumps([{"key": "k1", "severity": "blocking", "text": "redo"}]))
            lo("verdict", str(run), tid, "fail", "--findings", str(f))
        else:
            f.write_text("[]")
            lo("verdict", str(run), tid, "pass", "--findings", str(f))
    print("verified")
