import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import kw

KW = [sys.executable, str(Path(__file__).with_name("kw.py"))]


def run_kw(*args, check=True):
    p = subprocess.run(KW + [str(a) for a in args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(p.stderr)
    return p.returncode, json.loads(p.stdout or p.stderr)


class KwTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.tmp.name) / "run"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = Path(self.tmp.name) / name
        path.write_text(json.dumps(data))
        return path

    def to_executing(self, n=2, **init):
        flags = []
        for k, v in init.items():
            flags += [f"--{k.replace('_', '-')}", v]
        run_kw("init", self.run_dir, *flags)
        run_kw("phase", self.run_dir, "planning")
        tasks = [{"id": f"t{i}", "goal": f"goal {i}", "done_when": "x"} for i in range(1, n + 1)]
        run_kw("tasks", "set", self.run_dir, self.write("tasks.json", tasks))
        run_kw("phase", self.run_dir, "awaiting-approval")
        run_kw("approve", self.run_dir)

    def execute_all(self):
        while True:
            _, r = run_kw("claim", self.run_dir, "--owner", "w")
            if not r["claimed"]:
                break
            out = self.run_dir / "out" / f"{r['claimed']}.md"
            out.write_text("result")
            run_kw("done", self.run_dir, r["claimed"], "--output", f"out/{r['claimed']}.md")
        run_kw("phase", self.run_dir, "verifying")

    def findings(self, *keys):
        return self.write("f.json", [{"key": k, "severity": "blocking", "text": k} for k in keys])

    def test_gate1_requires_approval(self):
        run_kw("init", self.run_dir)
        run_kw("phase", self.run_dir, "awaiting-answers")
        run_kw("phase", self.run_dir, "planning")
        code, err = run_kw("phase", self.run_dir, "awaiting-approval", check=False)
        self.assertEqual(code, 2)  # no tasks yet
        run_kw("tasks", "set", self.run_dir, self.write("t.json", [{"id": "a", "goal": "g", "done_when": "d"}]))
        run_kw("phase", self.run_dir, "awaiting-approval")
        code, _ = run_kw("phase", self.run_dir, "executing", check=False)
        self.assertEqual(code, 2)  # must use approve
        code, _ = run_kw("claim", self.run_dir, "--owner", "w", check=False)
        self.assertEqual(code, 2)

    def test_happy_path(self):
        self.to_executing()
        self.execute_all()
        run_kw("verdict", self.run_dir, "t1", "pass")
        run_kw("verdict", self.run_dir, "t2", "pass")
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["phase"], "done")

    def test_repair_then_pass(self):
        self.to_executing(n=1)
        self.execute_all()
        _, r = run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("a"))
        self.assertEqual(r["status"], "todo")
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["phase"], "executing")
        _, c = run_kw("claim", self.run_dir, "--owner", "w")
        self.assertEqual(c["last_findings"][0]["key"], "a")
        run_kw("done", self.run_dir, "t1")
        run_kw("phase", self.run_dir, "verifying")
        run_kw("verdict", self.run_dir, "t1", "pass")
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["phase"], "done")

    def test_stall_on_repeated_finding(self):
        self.to_executing(n=1)
        self.execute_all()
        run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("a"))
        run_kw("finish", self.run_dir)
        self.execute_all()
        _, r = run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("a", "b"))
        self.assertEqual(r["status"], "needs-human")
        self.assertIn("stalled", r["stop_reason"])
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["phase"], "partial")

    def test_repair_cap(self):
        self.to_executing(n=1, max_repairs=1, max_rounds=5)
        self.execute_all()
        run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("a"))
        run_kw("finish", self.run_dir)
        self.execute_all()
        _, r = run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("b"))
        self.assertEqual(r["status"], "needs-human")
        self.assertIn("repair cap", r["stop_reason"])

    def test_round_cap(self):
        self.to_executing(n=1, max_repairs=9, max_rounds=2)
        self.execute_all()
        run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("a"))
        run_kw("finish", self.run_dir)
        self.execute_all()
        run_kw("verdict", self.run_dir, "t1", "fail", "--findings", self.findings("b"))
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["phase"], "partial")
        _, s = run_kw("status", self.run_dir)
        self.assertIn("round cap", s["tasks"][0]["stop_reason"])

    def test_verifier_cannot_pass_with_blocking_or_fail_without(self):
        self.to_executing(n=1)
        self.execute_all()
        code, _ = run_kw("verdict", self.run_dir, "t1", "pass", "--findings", self.findings("a"), check=False)
        self.assertEqual(code, 2)
        notes = self.write("n.json", [{"key": "n", "severity": "note", "text": "minor"}])
        code, _ = run_kw("verdict", self.run_dir, "t1", "fail", "--findings", notes, check=False)
        self.assertEqual(code, 2)

    def test_crash_recovery_releases_expired_lease_without_attempt(self):
        self.to_executing(n=2)
        run_kw("claim", self.run_dir, "--owner", "w1", "--lease", "1")
        time.sleep(1.2)
        _, r = run_kw("resume", self.run_dir)
        self.assertEqual(r["released"], ["t1"])
        _, s = run_kw("status", self.run_dir)
        self.assertEqual(s["tasks"][0]["status"], "todo")
        self.assertEqual(s["tasks"][0]["attempts"], 0)

    def test_snapshot_loss_and_torn_ledger_line(self):
        self.to_executing(n=2)
        run_kw("claim", self.run_dir, "--owner", "w")
        (self.run_dir / "state.json").unlink()
        with open(self.run_dir / "ledger.jsonl", "a") as fh:
            fh.write('{"type": "done", "id"')  # crash mid-append
        _, s = run_kw("status", self.run_dir)
        self.assertEqual(s["counts"]["running"], 1)
        self.assertEqual(s["counts"]["todo"], 1)

    def test_parallel_claims_never_duplicate(self):
        self.to_executing(n=8)
        with ThreadPoolExecutor(8) as ex:
            results = list(ex.map(lambda i: run_kw("claim", self.run_dir, "--owner", f"w{i}")[1]["claimed"], range(8)))
        self.assertEqual(sorted(results), sorted(f"t{i}" for i in range(1, 9)))

    def test_plan_frozen_after_approval(self):
        self.to_executing(n=1)
        code, _ = run_kw("tasks", "set", self.run_dir, self.write("t.json", [{"id": "z", "goal": "g", "done_when": "d"}]), check=False)
        self.assertEqual(code, 2)

    def staged(self, max_rounds=3, max_repairs=2):
        run_kw("init", self.run_dir, "--max-rounds", max_rounds, "--max-repairs", max_repairs)
        run_kw("phase", self.run_dir, "planning")
        tasks = [{"id": "r1", "goal": "g", "done_when": "x"},
                 {"id": "r2", "goal": "g", "done_when": "x"},
                 {"id": "p1", "goal": "g", "done_when": "x", "depends_on": ["r1", "r2"]},
                 {"id": "c1", "goal": "g", "done_when": "x", "depends_on": ["p1"]}]
        run_kw("tasks", "set", self.run_dir, self.write("tasks.json", tasks))
        run_kw("phase", self.run_dir, "awaiting-approval")
        run_kw("approve", self.run_dir)

    def pass_all_done(self):
        _, s = run_kw("status", self.run_dir)
        for t in s["tasks"]:
            if t["status"] == "done":
                run_kw("verdict", self.run_dir, t["id"], "pass")
        return run_kw("finish", self.run_dir)[1]

    def test_dependencies_gate_claims_and_stages_do_not_use_rounds(self):
        self.staged(max_rounds=1)
        self.execute_all()  # only r1, r2 are claimable
        _, s = run_kw("status", self.run_dir)
        self.assertEqual([t["status"] for t in s["tasks"]], ["done", "done", "todo", "todo"])
        r = self.pass_all_done()
        self.assertEqual(r["ready"], ["p1"])
        self.execute_all()
        r = self.pass_all_done()
        self.assertEqual(r["ready"], ["c1"])
        self.execute_all()
        r = self.pass_all_done()
        self.assertEqual(r["phase"], "done")
        _, s = run_kw("status", self.run_dir)
        self.assertEqual(s["round"], 1)

    def test_needs_human_dependency_unblocks_dependents(self):
        self.staged(max_repairs=0)
        self.execute_all()
        run_kw("verdict", self.run_dir, "r1", "pass")
        _, r = run_kw("verdict", self.run_dir, "r2", "fail", "--findings", self.findings("a"))
        self.assertEqual(r["status"], "needs-human")
        _, r = run_kw("finish", self.run_dir)
        self.assertEqual(r["ready"], ["p1"])
        _, c = run_kw("claim", self.run_dir, "--owner", "w")
        self.assertEqual(c["dependencies"]["r2"]["status"], "needs-human")

    def test_dependency_cycle_rejected(self):
        run_kw("init", self.run_dir)
        run_kw("phase", self.run_dir, "planning")
        tasks = [{"id": "a", "goal": "g", "done_when": "x", "depends_on": ["b"]},
                 {"id": "b", "goal": "g", "done_when": "x", "depends_on": ["a"]}]
        code, _ = run_kw("tasks", "set", self.run_dir, self.write("t.json", tasks), check=False)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
