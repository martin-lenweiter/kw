"""Fake CLI agent for testing kw loop: acts as planner, worker or verifier."""
import json, os, re, subprocess, sys
from pathlib import Path

prompt = sys.argv[-1]
run = Path.cwd()
KW = [sys.executable, str(Path(__file__).with_name("kw.py"))]

def kw(*a):
    return subprocess.run(KW + list(a), capture_output=True, text=True, check=True)

if "kw planner" in prompt:
    phase = json.loads(kw("status", str(run)).stdout)["phase"]
    if phase == "clarifying":
        kw("phase", str(run), "planning")
    tasks = [{"id": "t1", "goal": "g1", "done_when": "file says ok", "model": "fast"},
             {"id": "t2", "goal": "g2", "done_when": "file says ok"},
             {"id": "acc", "goal": "check all", "done_when": "both ok", "depends_on": ["t1", "t2"],
              "acceptance": True, "model": "strong"}]
    (run / "tasks.json").write_text(json.dumps(tasks))
    (run / "plan.md").write_text("plan")
    kw("tasks", "set", str(run), str(run / "tasks.json"))
    kw("phase", str(run), "awaiting-approval")
    print("planned")
elif "kw worker for task" in prompt:
    tid = re.search(r"kw worker for task (\S+)", prompt).group(1)
    out = run / "out" / f"{tid}.md"
    out.write_text("ok")
    (run / "out" / f"{tid}.calls").open("a").write(sys.argv[-2] + "\n")  # records model used
    print(f"KW_OUTPUT: out/{tid}.md")
elif "kw verifier for task" in prompt:
    tid = re.search(r"verifier for task (\S+)", prompt).group(1)
    marker = run / "out" / f"{tid}.failed-once"
    f = run / "verify" / f"{tid}.json"
    f.parent.mkdir(exist_ok=True)
    if tid == "t1" and not marker.exists():
        marker.write_text("x")
        f.write_text(json.dumps([{"key": "k1", "severity": "blocking", "text": "redo"}]))
        kw("verdict", str(run), tid, "fail", "--findings", str(f))
    else:
        f.write_text("[]")
        kw("verdict", str(run), tid, "pass", "--findings", str(f))
    print("verified")
