"""Agent Batch — plugin backend API routes, mounted at /api/plugins/agent-batch/.

Thin FastAPI layer over ``agent_batch_state`` (loaded from the package root beside the
``dashboard/`` dir), which is the same module the agent tools in ``__init__.py`` drive —
the panel and Hermes therefore cannot drift apart on what a plan looks like.

  GET   /plan         — stored tasks, repo, model, phases, per-phase status
  POST  /plan         — store the raw task list (phasing clears until Hermes or the panel sets it)
  POST  /plan/phase   — persist a phased plan (task indices or task text)
  POST  /plan/reset   — forget the plan and the locally recorded dispatches
  POST  /dispatch     — launch ONE phase via GitHub workflow_dispatch
  GET   /runs         — recent workflow runs + open PRs for the repo
  GET   /status       — plan + locally recorded dispatches in one call

State is per-profile under ``<hermes home>/plugin-data/agent-batch/`` (override the
directory with ``AGENT_BATCH_STATE``). GitHub calls use ``GITHUB_TOKEN`` resolved through
Hermes' own credential scope, and stdlib ``urllib`` — no external deps.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException

_STATE_PATH = Path(__file__).resolve().parent.parent / "agent_batch_state.py"
_spec = importlib.util.spec_from_file_location("agent_batch_state", _STATE_PATH)
if _spec is None or _spec.loader is None:  # pragma: no cover - packaging defect
    raise RuntimeError(f"cannot load {_STATE_PATH}")
_mod = importlib.util.module_from_spec(_spec)
sys.modules["agent_batch_state"] = _mod
_spec.loader.exec_module(_mod)
st = _mod

router = APIRouter()


def _call(fn, *args, **kwargs):
    """Run a state helper, turning an operator-facing failure into a 400."""
    try:
        return fn(*args, **kwargs)
    except st.AgentBatchError as e:
        raise HTTPException(400, str(e))


@router.get("/plan")
async def get_plan():
    return st.load_plan()


@router.post("/plan")
async def save_tasks(body: dict):
    """Store the raw task list. The phasing decision is a separate call."""
    tasks = body.get("tasks", [])
    if not isinstance(tasks, list) or not tasks:
        raise HTTPException(400, "tasks: non-empty list required")
    plan = st.load_plan()
    plan["tasks"] = [str(t).strip() for t in tasks if str(t).strip()]
    if "repo" in body:
        plan["repo"] = st.require_repo(str(body["repo"]))
    if "model" in body:
        plan["model"] = str(body.get("model") or "").strip()
    plan["phases"] = []  # changing the task list invalidates the old phasing
    plan["phase_status"] = []
    st.save_plan(plan)
    return {"ok": True, "task_count": len(plan["tasks"])}


@router.post("/plan/phase")
async def save_phases(body: dict):
    """Persist a phased plan: groups of parallel tasks, in execution order."""
    plan = _call(st.set_phases, st.load_plan(), body.get("phases", []))
    return {"ok": True, "phase_count": len(plan["phases"])}


@router.post("/plan/reset")
async def reset_plan():
    st.save_plan(st.empty_plan())
    st.save_json(st.RUNS_FILE, {"runs": []})
    return {"ok": True}


@router.post("/dispatch")
async def dispatch_phase(body: dict):
    """Launch one phase (index) via GitHub workflow_dispatch."""
    return _call(
        st.dispatch_phase,
        st.load_plan(),
        int(body.get("phase", -1)),
        repo=str(body.get("repo") or ""),
        model=str(body.get("model") or ""),
        context=str(body.get("context") or ""),
        base_branch=str(body.get("base_branch") or "main"),
        workflow=str(body.get("workflow") or ""),
    )


@router.get("/runs")
async def get_runs(repo: str = ""):
    """Recent workflow runs + open PRs. A repo the operator hasn't named is a 400
    with instructions, not a silent fall-through to someone else's default."""
    target = repo or st.load_plan().get("repo", "")
    return _call(st.github_activity, target)


@router.get("/status")
async def status():
    return {"plan": st.load_plan(), "local_runs": st.load_json(st.RUNS_FILE, {"runs": []})["runs"]}
