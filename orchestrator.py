#!/usr/bin/env python3
"""
orchestrator.py — Hermes-side CLI for the Agent Batch orchestrator.

The desktop plugin is the operator console; this is the agent's own tool for
phasing and dispatching without the UI.

Usage:
  orchestrator.py plan --repo owner/repo --model opencode/mimo-v2.5-free --tasks "t1\nt2" --phases '[[0,1],[2]]'
      # --phases is a JSON list of task-index groups; Hermes decides the grouping.
  orchestrator.py dispatch --phase 0 --context "project memory..."
      # launch one phase via GitHub workflow_dispatch
  orchestrator.py status --repo owner/repo
      # recent workflow runs + open PRs

State: ~/.hermes/agent-batch/plan.json (same files the plugin backend uses).
Token: GITHUB_TOKEN from env or ~/.hermes/.env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

STATE_DIR = Path(os.environ.get("AGENT_BATCH_STATE", str(Path.home() / ".hermes/agent-batch"))).resolve()
PLAN_FILE = STATE_DIR / "plan.json"
DEFAULT_REPO = "raaaas/agent-batch"
DEFAULT_WORKFLOW = "agent-batch.yml"


def load_plan() -> dict:
    if PLAN_FILE.exists():
        return json.loads(PLAN_FILE.read_text())
    return {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": "opencode/mimo-v2.5-free"}


def save_plan(plan: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_FILE.write_text(json.dumps(plan, indent=2, ensure_ascii=False))


def gh_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        return tok
    env_file = Path.home() / ".hermes/.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def gh(method: str, url: str, body: dict | None = None, timeout: int = 30):
    token = gh_token()
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "hermes-agent-batch"}
    data = json.dumps(body).encode() if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"ERROR: github {e.code}: {e.read().decode(errors='replace')[:400]}", file=sys.stderr)
        sys.exit(1)


def cmd_plan(args) -> None:
    tasks = [t.strip() for t in args.tasks.splitlines() if t.strip() and not t.strip().startswith("#")]
    if not tasks:
        print("ERROR: no tasks", file=sys.stderr)
        sys.exit(1)

    if args.phases:
        groups = json.loads(args.phases)
        # resolve indices to task strings, validate bounds
        phases = []
        for g in groups:
            grp = []
            for idx in g:
                if not (0 <= idx < len(tasks)):
                    print(f"ERROR: task index {idx} out of range (0..{len(tasks)-1})", file=sys.stderr)
                    sys.exit(1)
                grp.append(tasks[idx])
            phases.append(grp)
    else:
        # default: everything in one parallel phase
        phases = [tasks]

    plan = load_plan()
    plan.update({
        "tasks": tasks,
        "phases": phases,
        "phase_status": ["pending"] * len(phases),
        "repo": args.repo or plan.get("repo", DEFAULT_REPO),
        "model": args.model or plan.get("model", "opencode/mimo-v2.5-free"),
    })
    save_plan(plan)

    print(f"Plan saved: {len(tasks)} tasks in {len(phases)} phase(s)")
    for i, p in enumerate(phases, 1):
        print(f"  Phase {i} ({len(p)} parallel):")
        for t in p:
            print(f"    - {t}")


def cmd_dispatch(args) -> None:
    plan = load_plan()
    phases = plan.get("phases", [])
    if not phases:
        print("ERROR: no plan — run `plan` first", file=sys.stderr)
        sys.exit(1)
    idx = args.phase
    if not (0 <= idx < len(phases)):
        print(f"ERROR: phase {idx} out of range (0..{len(phases)-1})", file=sys.stderr)
        sys.exit(1)

    repo = plan.get("repo", DEFAULT_REPO)
    tasks = phases[idx]
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{DEFAULT_WORKFLOW}/dispatches"
    gh("POST", url, {
        "ref": args.base or "main",
        "inputs": {
            "tasks": "\n".join(tasks),
            "context": args.context or "",
            "model": plan.get("model", "opencode/mimo-v2.5-free"),
            "base_branch": args.base or "main",
        },
    })
    status = list(plan.setdefault("phase_status", ["pending"] * len(phases)))
    status[idx] = "running"
    plan["phase_status"] = status
    save_plan(plan)
    print(f"Dispatched phase {idx} ({len(tasks)} tasks) to {repo}")


def cmd_status(args) -> None:
    repo = args.repo or load_plan().get("repo", DEFAULT_REPO)
    data = gh("GET", f"https://api.github.com/repos/{repo}/actions/runs?per_page=8")
    print(f"=== workflow runs ({repo}) ===")
    for r in data.get("workflow_runs", []):
        print(f"  #{r['id']} {r.get('name','?'):30s} {r.get('status'):12s} {r.get('conclusion') or ''}  {r.get('head_branch')}")
    data = gh("GET", f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=10")
    print("=== open PRs ===")
    for p in data:
        print(f"  #{p['number']} {p['title'][:60]}  [{p['head']['ref']}]")


def main() -> int:
    p = argparse.ArgumentParser(description="Agent Batch orchestrator CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--tasks", required=True, help="task list (newline separated)")
    p_plan.add_argument("--phases", default="", help='JSON list of index groups, e.g. [[0,1],[2,3]]')
    p_plan.add_argument("--repo", default="")
    p_plan.add_argument("--model", default="")
    p_plan.set_defaults(fn=cmd_plan)

    p_disp = sub.add_parser("dispatch")
    p_disp.add_argument("--phase", type=int, required=True)
    p_disp.add_argument("--context", default="")
    p_disp.add_argument("--base", default="main")
    p_disp.set_defaults(fn=cmd_dispatch)

    p_status = sub.add_parser("status")
    p_status.add_argument("--repo", default="")
    p_status.set_defaults(fn=cmd_status)

    args = p.parse_args()
    args.fn(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
