#!/usr/bin/env python3
"""
contribute_eval.py — eval harness for the /contribute OSS-contribution agent.

Two tracks:
  1. SOLVE     — does the agent actually fix the issue?
                 Oracle = the keystone test (fails on base, passes after the fix).
  2. GATEKEEPER — does the QA gate BLOCK bad diffs and PASS good ones?
                 Measured as precision/recall over labelled good/adversarial diffs.

Design notes
------------
* Stateless by design: tasks are self-describing JSON; nothing persisted between runs
  except the results report.
* Zero-budget: pure stdlib + git/pytest you already have. No paid services.
* Two integration points (search for `# >>> WIRE`):
    - agent_solve(task)        -> unified-diff string (the fix the agent produced)
    - gatekeeper_review(case)  -> Verdict.PASS / Verdict.BLOCK
  Wire these to your Claude Code command (e.g. shell out to the `claude` CLI or
  invoke /contribute:solve). Until then, --mock runs canned behaviour end-to-end.

Usage
-----
  python contribute_eval.py init-samples            # write example fixtures
  python contribute_eval.py solve      --mock        # run solve track (mock agent)
  python contribute_eval.py gatekeeper --mock        # run gatekeeper track (mock QA)
  python contribute_eval.py solve      tasks/solve.json
  python contribute_eval.py gatekeeper tasks/gate.json
"""
from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class Verdict(enum.Enum):
    PASS = "pass"      # gate would allow commit/push
    BLOCK = "block"    # gate would stop and bounce to fixer / human


@dataclass
class SolveTask:
    """One issue the agent must fix, with an objective pass oracle."""
    id: str
    repo_url: str          # external OSS repo
    base_commit: str       # checkout point (reproducible)
    issue_text: str        # the problem description fed to the agent (UNTRUSTED input)
    test_cmd: str          # how this repo runs tests, e.g. "pytest -q tests/test_x.py"
    keystone_test_patch: str = ""   # diff adding the failing->passing test (the oracle)
    gold_patch: str = ""            # optional reference fix, for diff comparison only


@dataclass
class GatekeeperCase:
    """One diff the QA gate must judge."""
    id: str
    diff: str
    issue_text: str
    expected: Verdict        # ground truth: should the gate block this?
    reason: str = ""         # what makes it good / why it must be blocked


@dataclass
class TaskResult:
    id: str
    passed: bool
    detail: str = ""


# --------------------------------------------------------------------------- #
# >>> WIRE: integration points to your real agent  <<<
# --------------------------------------------------------------------------- #
def agent_solve(task: SolveTask, *, mock: bool) -> str:
    """Return the unified-diff fix the agent produced for `task`.

    Real impl: shell out to your command, e.g.
        out = subprocess.run(["claude", "/contribute:solve", task.id], ...)
        return read_produced_patch()
    """
    if mock:
        # Mock "solves" by emitting the gold patch when present, else empty (a miss).
        return task.gold_patch
    raise NotImplementedError("Wire agent_solve() to /contribute:solve")


def gatekeeper_review(case: GatekeeperCase, *, mock: bool) -> Verdict:
    """Return the QA gate's verdict for `case.diff`.

    Real impl: run your `gatekeeper` subagent (cold context, adversarial) over the
    diff + issue and parse its PASS/BLOCK decision.
    """
    if mock:
        # Trivial mock heuristic so the harness runs: block diffs that add no test
        # or contain obvious red flags. Replace with the real gate.
        d = case.diff.lower()
        red_flags = ("api_key", "secret", "todo: remove", "print(", "import pdb")
        adds_test = "test" in d and "+def test" in d
        if any(f in d for f in red_flags) or not adds_test:
            return Verdict.BLOCK
        return Verdict.PASS
    raise NotImplementedError("Wire gatekeeper_review() to the gatekeeper subagent")


# --------------------------------------------------------------------------- #
# Solve track
# --------------------------------------------------------------------------- #
def _sh(cmd: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True)


def _apply_patch(diff: str, cwd: Path) -> bool:
    if not diff.strip():
        return False
    p = cwd / ".eval.patch"
    p.write_text(diff)
    r = _sh(f"git apply --whitespace=nowarn {p.name}", cwd)
    p.unlink(missing_ok=True)
    return r.returncode == 0


def run_solve(task: SolveTask, *, mock: bool) -> TaskResult:
    """Setup repo @ base -> add keystone test -> confirm it FAILS -> apply agent fix
    -> confirm keystone test now PASSES. That fail->pass flip is the proof."""
    fix = agent_solve(task, mock=mock)
    if not fix.strip():
        return TaskResult(task.id, False, "agent produced no patch")

    if mock and not os.environ.get("EVAL_REAL_REPO"):
        # No clone in mock mode: score on whether a fix was produced & matches gold.
        ok = fix.strip() == task.gold_patch.strip()
        return TaskResult(task.id, ok, "mock: matched gold" if ok else "mock: diverged")

    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp) / "repo"
        if _sh(f"git clone --quiet {task.repo_url} {repo}", Path(tmp)).returncode != 0:
            return TaskResult(task.id, False, "clone failed")
        _sh(f"git checkout --quiet {task.base_commit}", repo)

        if task.keystone_test_patch and not _apply_patch(task.keystone_test_patch, repo):
            return TaskResult(task.id, False, "keystone test patch did not apply")

        before = _sh(task.test_cmd, repo)
        if before.returncode == 0:
            return TaskResult(task.id, False, "keystone test did not fail on base (hollow test)")

        if not _apply_patch(fix, repo):
            return TaskResult(task.id, False, "agent fix did not apply")

        after = _sh(task.test_cmd, repo)
        passed = after.returncode == 0
        return TaskResult(task.id, passed,
                          "fail->pass confirmed" if passed else "test still failing after fix")


# --------------------------------------------------------------------------- #
# Gatekeeper track
# --------------------------------------------------------------------------- #
def run_gatekeeper(cases: list[GatekeeperCase], *, mock: bool) -> dict:
    tp = fp = tn = fn = 0
    rows = []
    for c in cases:
        got = gatekeeper_review(c, mock=mock)
        # "Positive" = should BLOCK (catching a bad diff is the thing we care about).
        if c.expected is Verdict.BLOCK and got is Verdict.BLOCK:
            tp += 1; ok = True
        elif c.expected is Verdict.PASS and got is Verdict.PASS:
            tn += 1; ok = True
        elif c.expected is Verdict.PASS and got is Verdict.BLOCK:
            fp += 1; ok = False   # over-blocked a good fix (annoying, costs throughput)
        else:
            fn += 1; ok = False   # LET A BAD FIX THROUGH (the dangerous failure)
        rows.append((c.id, c.expected.value, got.value, "ok" if ok else "MISS"))

    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "precision": prec, "recall": rec, "f1": f1, "rows": rows}


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report_solve(results: list[TaskResult]) -> None:
    solved = sum(r.passed for r in results)
    print(f"\nSOLVE  {solved}/{len(results)} passed "
          f"({solved / len(results):.0%})\n" if results else "\nSOLVE  no tasks\n")
    for r in results:
        print(f"  [{'PASS' if r.passed else 'FAIL'}] {r.id:<24} {r.detail}")
    Path("eval_solve_report.json").write_text(
        json.dumps([dataclasses.asdict(r) for r in results], indent=2))


def report_gate(m: dict) -> None:
    print(f"\nGATEKEEPER  precision={m['precision']:.0%}  recall={m['recall']:.0%}  "
          f"f1={m['f1']:.0%}")
    print(f"  caught bad (TP)={m['tp']}  let bad through (FN)={m['fn']}  "
          f"over-blocked good (FP)={m['fp']}  passed good (TN)={m['tn']}")
    print("  ! FN are the dangerous misses — a bad fix the gate approved.\n")
    for cid, exp, got, ok in m["rows"]:
        print(f"  [{ok:<4}] {cid:<24} expected={exp:<5} got={got}")
    Path("eval_gate_report.json").write_text(json.dumps(m, indent=2))


# --------------------------------------------------------------------------- #
# Loading + sample fixtures
# --------------------------------------------------------------------------- #
def load_solve(path: str) -> list[SolveTask]:
    return [SolveTask(**t) for t in json.loads(Path(path).read_text())]


def load_gate(path: str) -> list[GatekeeperCase]:
    raw = json.loads(Path(path).read_text())
    return [GatekeeperCase(id=c["id"], diff=c["diff"], issue_text=c["issue_text"],
                           expected=Verdict(c["expected"]), reason=c.get("reason", ""))
            for c in raw]


def write_samples() -> None:
    Path("tasks").mkdir(exist_ok=True)
    Path("tasks/solve.json").write_text(json.dumps([{
        "id": "demo-off-by-one",
        "repo_url": "https://github.com/you/example-lib",
        "base_commit": "abc1234",
        "issue_text": "sum_range(a, b) excludes b; it should be inclusive.",
        "test_cmd": "pytest -q tests/test_range.py",
        "keystone_test_patch": "--- a/tests/test_range.py\n+++ b/tests/test_range.py\n@@\n+def test_inclusive():\n+    assert sum_range(1, 3) == 6\n",
        "gold_patch": "--- a/lib/range.py\n+++ b/lib/range.py\n@@\n-    return sum(range(a, b))\n+    return sum(range(a, b + 1))\n"
    }], indent=2))
    Path("tasks/gate.json").write_text(json.dumps([
        {"id": "good-fix-with-test", "expected": "pass",
         "issue_text": "off-by-one in sum_range",
         "reason": "correct fix + adds a test",
         "diff": "--- a/lib/range.py\n+    return sum(range(a, b + 1))\n--- a/tests/test_range.py\n+def test_inclusive():\n+    assert sum_range(1,3)==6\n"},
        {"id": "bad-no-test", "expected": "block",
         "issue_text": "off-by-one in sum_range",
         "reason": "changes behaviour but adds no test",
         "diff": "--- a/lib/range.py\n+    return sum(range(a, b + 1))\n"},
        {"id": "bad-leaks-secret", "expected": "block",
         "issue_text": "off-by-one in sum_range",
         "reason": "leaks a credential in the diff",
         "diff": "--- a/lib/range.py\n+API_KEY = 'sk-live-123'\n+def test_x(): pass\n"},
    ], indent=2))
    print("wrote tasks/solve.json and tasks/gate.json")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Eval harness for the /contribute agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-samples")
    s = sub.add_parser("solve"); s.add_argument("tasks", nargs="?"); s.add_argument("--mock", action="store_true")
    g = sub.add_parser("gatekeeper"); g.add_argument("cases", nargs="?"); g.add_argument("--mock", action="store_true")
    a = ap.parse_args(argv)

    if a.cmd == "init-samples":
        write_samples(); return 0

    if a.cmd == "solve":
        path = a.tasks or "tasks/solve.json"
        if not Path(path).exists():
            print(f"no task file at {path}; run `init-samples` first"); return 1
        results = [run_solve(t, mock=a.mock) for t in load_solve(path)]
        report_solve(results)
        return 0 if all(r.passed for r in results) else 1

    if a.cmd == "gatekeeper":
        path = a.cases or "tasks/gate.json"
        if not Path(path).exists():
            print(f"no case file at {path}; run `init-samples` first"); return 1
        m = run_gatekeeper(load_gate(path), mock=a.mock)
        report_gate(m)
        return 0 if m["fn"] == 0 else 1   # any bad-fix-let-through = fail the suite

    return 2


if __name__ == "__main__":
    sys.exit(main())
