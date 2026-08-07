"""Agent Batch — plugin backend API routes.

Mounted at /api/plugins/agent-batch/ by the dashboard plugin system.

The plugin is the OPERATOR CONSOLE for the Agent Batch orchestrator:

  - POST /plan          — store raw tasks; the orchestrator (Hermes agent)
                          later calls /plan/phase to commit a phased plan.
  - POST /plan/phase    — persist a phased plan (phases: list of lists of tasks).
  - GET  /plan          — read the stored plan.
  - POST /dispatch      — launch ONE phase via GitHub workflow_dispatch.
  - GET  /runs          — poll workflow runs + PRs for the repo.
  - GET  /status        — everything in one call (plan + runs + PRs).

State lives in JSON files under $AGENT_BATCH_STATE (default
~/.hermes/agent-batch/). GitHub calls use the token from $GITHUB_TOKEN
(read from ~/.hermes/.env when unset) and urllib — no external deps.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

STATE_DIR = Path(os.environ.get("AGENT_BATCH_STATE", str(Path.home() / ".hermes/agent-batch"))).resolve()
STATE_DIR.mkdir(parents=True, exist_ok=True)

PLAN_FILE = STATE_DIR / "plan.json"
RUNS_FILE = STATE_DIR / "runs.json"

DEFAULT_REPO = os.environ.get("AGENT_BATCH_REPO", "raaaas/agent-batch")
DEFAULT_WORKFLOW = os.environ.get("AGENT_BATCH_WORKFLOW", "agent-batch.yml")
DEFAULT_MODEL = os.environ.get("AGENT_BATCH_MODEL", "opencode/mimo-v2.5-free")

router = APIRouter()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _github_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "")
    if tok:
        return tok
    env_file = Path.home() / ".hermes/.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("GITHUB_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _gh_request(method: str, url: str, body: dict | None = None, timeout: int = 20):
    token = _github_token()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "hermes-agent-batch",
    }
    data = json.dumps(body).encode() if body is not None else None
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise HTTPException(e.code, f"github api {e.code}: {detail}")


def _err(e: Exception) -> HTTPException:
    if isinstance(e, HTTPException):
        return e
    return HTTPException(500, str(e))


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

@router.get("/plan")
async def get_plan():
    return _load_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})


@router.post("/plan")
async def save_tasks(body: dict):
    """Store the raw task list (one task per entry). Orchestrator phases later."""
    tasks = body.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise HTTPException(400, "tasks: non-empty list required")
    plan = _load_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})
    plan["tasks"] = [str(t).strip() for t in tasks if str(t).strip()]
    plan["repo"] = str(body.get("repo") or plan.get("repo") or DEFAULT_REPO)
    plan["model"] = str(body.get("model") or plan.get("model") or DEFAULT_MODEL)
    plan["phases"] = []  # invalidate old phasing when tasks change
    _save_json(PLAN_FILE, plan)
    return {"ok": True, "task_count": len(plan["tasks"])}


@router.post("/plan/phase")
async def save_phases(body: dict):
    """Persist a phased plan: phases = [[task, task], [task], ...] (parallel groups)."""
    phases = body.get("phases", [])
    if not isinstance(phases, list) or not phases:
        raise HTTPException(400, "phases: non-empty list of task lists required")
    plan = _load_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})
    plan["phases"] = phases
    plan["phase_status"] = ["pending"] * len(phases)
    _save_json(PLAN_FILE, plan)
    return {"ok": True, "phase_count": len(phases)}


@router.post("/plan/reset")
async def reset_plan():
    _save_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})
    _save_json(RUNS_FILE, {"runs": []})
    return {"ok": True}


# ---------------------------------------------------------------------------
# dispatch + status
# ---------------------------------------------------------------------------

@router.post("/dispatch")
async def dispatch_phase(body: dict):
    """Launch one phase (index) via GitHub workflow_dispatch."""
    plan = _load_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})
    repo = str(body.get("repo") or plan.get("repo") or DEFAULT_REPO)
    idx = int(body.get("phase", -1))
    phases = plan.get("phases", [])
    if idx < 0 or idx >= len(phases):
        raise HTTPException(400, f"phase index {idx} out of range (0..{len(phases)-1})")
    tasks = phases[idx]
    if not tasks:
        raise HTTPException(400, f"phase {idx} is empty")

    workflow = str(body.get("workflow") or DEFAULT_WORKFLOW)
    model = str(body.get("model") or plan.get("model") or DEFAULT_MODEL)
    base = str(body.get("base_branch") or "main")
    context = str(body.get("context") or "")

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    _gh_request("POST", url, {
        "ref": base,
        "inputs": {
            "tasks": "\n".join(tasks),
            "context": context,
            "model": model,
            "base_branch": base,
        },
    })

    # record the run locally
    runs = _load_json(RUNS_FILE, {"runs": []})
    runs["runs"].append({
        "phase": idx,
        "tasks": tasks,
        "repo": repo,
        "model": model,
        "base_branch": base,
        "dispatched_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    })
    _save_json(RUNS_FILE, runs)

    status = list(plan.setdefault("phase_status", ["pending"] * len(phases)))
    if idx < len(status):
        status[idx] = "running"
    plan["phase_status"] = status
    _save_json(PLAN_FILE, plan)

    return {"ok": True, "phase": idx, "task_count": len(tasks), "repo": repo}


@router.get("/runs")
async def get_runs(repo: str = ""):
    """Poll GitHub for workflow runs + open PRs for the repo."""
    repo = repo or DEFAULT_REPO
    token = _github_token()
    if not token:
        return {"runs": [], "prs": [], "error": "no GITHUB_TOKEN configured"}

    out = {"runs": [], "prs": []}

    # recent workflow runs
    try:
        url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=10"
        data = _gh_request("GET", url)
        out["runs"] = [
            {
                "id": r.get("id"),
                "name": r.get("name"),
                "status": r.get("status"),
                "conclusion": r.get("conclusion"),
                "head_branch": r.get("head_branch"),
                "created_at": r.get("created_at"),
                "html_url": r.get("html_url"),
            }
            for r in data.get("workflow_runs", [])
        ]
    except Exception as e:
        out["runs_error"] = str(e)

    # open PRs
    try:
        url = f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=20"
        data = _gh_request("GET", url)
        out["prs"] = [
            {
                "number": p.get("number"),
                "title": p.get("title"),
                "head": (p.get("head") or {}).get("ref"),
                "created_at": p.get("created_at"),
                "html_url": p.get("html_url"),
            }
            for p in data
        ]
    except Exception as e:
        out["prs_error"] = str(e)

    return out


@router.get("/status")
async def status():
    plan = _load_json(PLAN_FILE, {"tasks": [], "phases": [], "repo": DEFAULT_REPO, "model": DEFAULT_MODEL})
    runs = _load_json(RUNS_FILE, {"runs": []})
    return {"plan": plan, "local_runs": runs["runs"]}
