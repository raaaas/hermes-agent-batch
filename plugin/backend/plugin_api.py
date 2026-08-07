"""Agent Batch — plugin backend API routes.

Mounted at /api/plugins/agent-batch/ by the dashboard plugin system.

What this plugin does:
  - Stores task lists + phase plans as JSON under $HERMES_AGENT_BATCH_ROOT
    (default ~/.hermes/agent-batch/).
  - Dispatches GitHub Actions workflows (agent-batch.yml) via the GitHub API.
  - Polls PR status so the desktop UI can show live progress.

The PHASING itself (grouping tasks into dependency-ordered phases and writing
the shared CONTEXT.md) is done by the Hermes agent, not by this router — the
router stores whatever plan the agent commits to disk.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import APIRouter, HTTPException

ROOT = Path(os.environ.get("HERMES_AGENT_BATCH_ROOT", str(Path.home() / ".hermes/agent-batch"))).resolve()
ROOT.mkdir(parents=True, exist_ok=True)

router = APIRouter()

# GitHub API — token from env, then ~/.git-credentials (has repo+workflow
# scopes), then the Hermes .env as a last resort.
def _gh_token() -> str:
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        return tok
    # credential stored by git credential.helper — full repo+workflow scopes
    cred_path = Path.home() / ".git-credentials"
    if cred_path.exists():
        for line in cred_path.read_text().splitlines():
            if "github.com" in line and "://" in line:
                try:
                    tok = line.split("://", 1)[1].split("@", 1)[0].split(":", 1)[1]
                    if tok:
                        return tok
                except Exception:
                    continue
    env_path = Path.home() / ".hermes" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("GITHUB_TOKEN="):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                if tok:
                    return tok
    return ""


def _gh(method: str, url: str, body: dict | None = None, timeout: int = 30) -> dict:
    token = _gh_token()
    if not token:
        raise HTTPException(500, "GITHUB_TOKEN not found in ~/.hermes/.env")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:300]
        raise HTTPException(e.code, f"github api {e.code}: {msg}")


def _err(e: Exception) -> HTTPException:
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, (ValueError, json.JSONDecodeError)):
        return HTTPException(400, str(e))
    return HTTPException(500, str(e))


def _store(name: str, data: dict) -> None:
    (ROOT / f"{name}.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _load(name: str) -> dict:
    p = ROOT / f"{name}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------
# Plan / tasks
# ---------------------------------------------------------------------------

@router.get("/plan")
async def get_plan():
    return _load("plan")


@router.post("/plan")
async def save_plan(body: dict):
    """Save the phased plan. Expects:
    {repo, tasks: [...], phases: [[task, ...], ...], context: "..."}
    """
    plan = {
        "repo": str(body.get("repo", "")).strip(),
        "tasks": [str(t) for t in (body.get("tasks") or [])],
        "phases": [[str(t) for t in ph] for ph in (body.get("phases") or [])],
        "context": str(body.get("context", "")),
        "updated": time.time(),
    }
    if not plan["repo"]:
        raise HTTPException(400, "repo required (owner/name)")
    _store("plan", plan)
    return {"ok": True, "plan": plan}


@router.get("/phases")
async def get_phases():
    return {"phases": _load("plan").get("phases", [])}


# ---------------------------------------------------------------------------
# Dispatch — fire the workflow for one phase
# ---------------------------------------------------------------------------

@router.post("/dispatch")
async def dispatch(body: dict):
    """Fire agent-batch.yml on the repo for the given phase tasks.
    body: {repo, tasks: [...], model?, base_branch?}
    """
    repo = str(body.get("repo", "")).strip()
    tasks = [str(t) for t in (body.get("tasks") or [])]
    if not repo or not tasks:
        raise HTTPException(400, "repo and tasks required")
    model = str(body.get("model") or "opencode/mimo-v2.5-free")
    base = str(body.get("base_branch") or "main")

    # The workflow is read from the repo's default branch; dispatch by file name.
    url = f"https://api.github.com/repos/{repo}/actions/workflows/agent-batch.yml/dispatches"
    _gh("POST", url, {
        "ref": base,
        "inputs": {
            "tasks": "\n".join(tasks),
            "model": model,
            "base_branch": base,
        },
    })
    return {"ok": True, "dispatched": tasks}


# ---------------------------------------------------------------------------
# Status — open PRs from agent-* branches
# ---------------------------------------------------------------------------

@router.get("/status/{repo}")
async def status(repo: str):
    """List open PRs whose head branch starts with 'agent-'."""
    url = f"https://api.github.com/repos/{repo}/pulls?state=open&per_page=50"
    pulls = _gh("GET", url)
    agent_prs = []
    for pr in pulls:
        head = (pr.get("head") or {}).get("ref", "")
        if head.startswith("agent-"):
            agent_prs.append({
                "number": pr.get("number"),
                "title": pr.get("title"),
                "branch": head,
                "created": (pr.get("created_at") or "")[:19],
                "url": pr.get("html_url"),
                "mergeable": pr.get("mergeable"),
            })
    return {"prs": agent_prs, "count": len(agent_prs)}
