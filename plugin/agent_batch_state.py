"""Agent Batch — state + GitHub helpers shared by both halves of the package.

The dashboard router (``dashboard/plugin_api.py`` → ``/api/plugins/agent-batch``) and
the agent tools (``__init__.py``) drive the same plan through this module, so the panel
and Hermes can never disagree about what a phased plan looks like.

State lives in the per-plugin data root (``<hermes home>/plugin-data/agent-batch/``,
or ``$AGENT_BATCH_STATE`` as a directory override) — never in the install tree, which
``hermes plugins update``/``remove`` rewrites. Nothing touches the filesystem at import.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PLUGIN_ID = "agent-batch"
DEFAULT_WORKFLOW = "agent-batch.yml"
PLAN_FILE = "plan.json"
RUNS_FILE = "runs.json"

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class AgentBatchError(RuntimeError):
    """A failure with a message that is safe — and useful — to show the operator."""


# --------------------------------------------------------------------------- paths


def data_root() -> Path:
    """Directory holding plan/runs. Resolved per call so it follows the active profile."""
    override = os.environ.get("AGENT_BATCH_STATE", "").strip()
    if override:
        return Path(override).expanduser()
    from plugins.plugin_storage import plugin_data_dir

    return plugin_data_dir(PLUGIN_ID)


def _path(name: str) -> Path:
    return data_root() / name


# --------------------------------------------------------------------------- defaults


def default_repo() -> str:
    return os.environ.get("AGENT_BATCH_REPO", "").strip()


def default_model() -> str:
    return os.environ.get("AGENT_BATCH_MODEL", "").strip()


def default_workflow() -> str:
    return os.environ.get("AGENT_BATCH_WORKFLOW", "").strip() or DEFAULT_WORKFLOW


def require_repo(repo: str) -> str:
    """Validate the repo the dispatch/runs calls are about to address.

    Empty is the normal state for a fresh install — the operator names the repo in the
    panel — so this reads as an instruction, not a stack of defaults.
    """
    repo = (repo or "").strip()
    if not repo:
        raise AgentBatchError(
            "no repo set — type owner/repo in the Agent Batch panel (or set AGENT_BATCH_REPO)"
        )
    if not _REPO_RE.match(repo):
        raise AgentBatchError(f"repo must look like owner/name, got {repo!r}")
    return repo


# --------------------------------------------------------------------------- json state


def load_json(name: str, default: Any) -> Any:
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_json(name: str, data: Any) -> None:
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def empty_plan() -> dict:
    return {"tasks": [], "phases": [], "phase_status": [], "repo": default_repo(), "model": default_model()}


def load_plan() -> dict:
    plan = load_json(PLAN_FILE, None)
    if not isinstance(plan, dict):
        return empty_plan()
    plan.setdefault("tasks", [])
    plan.setdefault("phases", [])
    plan.setdefault("phase_status", [])
    return plan


def save_plan(plan: dict) -> None:
    save_json(PLAN_FILE, plan)


# --------------------------------------------------------------------------- phasing


def normalize_phases(plan: dict, groups: Any) -> list[list[str]]:
    """Turn a phasing decision into parallel groups of task strings.

    Accepts task indices ([[0, 1], [2]] — what Hermes reports, cheapest to get right) or
    task text directly. Every task must land in exactly one group, so a dropped or
    invented task fails loudly instead of silently never running.
    """
    tasks = [str(t).strip() for t in plan.get("tasks", []) if str(t).strip()]
    if not isinstance(groups, list) or not groups:
        raise AgentBatchError("phases must be a non-empty list of groups")
    if not tasks:
        raise AgentBatchError("no tasks stored — save the task list first")

    phases: list[list[str]] = []
    used: set[int] = set()
    for group in groups:
        if not isinstance(group, list) or not group:
            raise AgentBatchError("each phase must be a non-empty list of tasks")
        resolved: list[str] = []
        for item in group:
            if isinstance(item, bool) or not isinstance(item, int):
                match = next((i for i, t in enumerate(tasks) if t == str(item).strip()), None)
                if match is None:
                    raise AgentBatchError(f"task not in the stored plan: {str(item)[:80]!r}")
                resolved.append(tasks[match])
                used.add(match)
                continue
            if not 0 <= item < len(tasks):
                raise AgentBatchError(f"task index {item} out of range (0..{len(tasks) - 1})")
            if item in used:
                raise AgentBatchError(f"task {item} ({tasks[item][:60]!r}) appears in two phases")
            used.add(item)
            resolved.append(tasks[item])
        phases.append(resolved)

    missing = [i for i in range(len(tasks)) if i not in used]
    if missing:
        raise AgentBatchError(f"tasks not placed in any phase: {missing}")
    return phases


def set_phases(plan: dict, groups: Any) -> dict:
    """Write a phased plan (phases + a pending status per phase) and persist it."""
    plan["phases"] = normalize_phases(plan, groups)
    plan["phase_status"] = ["pending"] * len(plan["phases"])
    save_plan(plan)
    return plan


# --------------------------------------------------------------------------- dispatch


def github_token() -> str:
    """GITHUB_TOKEN through Hermes' credential resolution (profile scope, then env)."""
    from agent.secret_scope import get_secret_str

    return get_secret_str("GITHUB_TOKEN")


def gh_request(method: str, url: str, body: dict | None = None, timeout: int = 20) -> Any:
    token = github_token()
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": f"hermes-{PLUGIN_ID}",
    }
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        raise AgentBatchError(f"github api {e.code}: {detail}") from e
    except (urllib.error.URLError, OSError) as e:
        raise AgentBatchError(f"github api unreachable: {e}") from e


def dispatch_phase(plan: dict, index: int, repo: str = "", model: str = "", context: str = "",
                   base_branch: str = "main", workflow: str = "") -> dict:
    """Launch one phase via workflow_dispatch, record it locally, mark it running."""
    phases = plan.get("phases", [])
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(phases):
        raise AgentBatchError(f"phase index {index} out of range (0..{len(phases) - 1})")
    tasks = phases[index]
    if not tasks:
        raise AgentBatchError(f"phase {index} is empty")

    repo = require_repo(repo or plan.get("repo", ""))
    if not github_token():
        raise AgentBatchError("no GITHUB_TOKEN set — Hermes cannot call the GitHub API without it")
    model = (model or plan.get("model", "") or default_model()).strip()
    workflow = (workflow or default_workflow()).strip()
    base = (base_branch or "main").strip()

    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    gh_request("POST", url, {
        "ref": base,
        "inputs": {
            "tasks": "\n".join(tasks),
            "context": context or "",
            "model": model,
            "base_branch": base,
        },
    })

    runs = load_json(RUNS_FILE, {"runs": []})
    if not isinstance(runs, dict) or not isinstance(runs.get("runs"), list):
        runs = {"runs": []}
    runs["runs"].append({
        "phase": index,
        "tasks": tasks,
        "repo": repo,
        "model": model,
        "base_branch": base,
        "dispatched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    save_json(RUNS_FILE, runs)

    status = list(plan.setdefault("phase_status", ["pending"] * len(phases)))
    status[index] = "running"
    plan["phase_status"] = status
    save_plan(plan)
    return {"ok": True, "phase": index, "task_count": len(tasks), "repo": repo}


def github_activity(repo: str) -> dict:
    """Recent workflow runs + open PRs for the repo (both best-effort)."""
    repo = require_repo(repo)
    if not github_token():
        raise AgentBatchError("no GITHUB_TOKEN set — Hermes cannot call the GitHub API without it")

    out: dict[str, Any] = {"repo": repo, "runs": [], "prs": []}
    try:
        data = gh_request("GET", f"https://api.github.com/repos/{repo}/actions/runs?per_page=10")
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
    except AgentBatchError as e:
        out["runs_error"] = str(e)

    try:
        data = gh_request("GET", f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=20")
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
    except AgentBatchError as e:
        out["prs_error"] = str(e)

    return out
