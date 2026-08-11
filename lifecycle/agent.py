#!/usr/bin/env python3
"""gitclaw-style issue agent for hermes-agent-batch.

Runs inside the agent-issues.yml workflow. One issue = one agent session:
  - state/issues/<N>.json holds the conversation history, committed to git
    (every comment on the issue resumes the same session)
  - the agent works on branch agent/issue-<N> and opens a PR
  - replies as an issue comment with a summary + PR link
  - 👀 while working, ✅ when done
  - optionally POSTs a run log to the Hermes project-manager panel
    (PM_PANEL_URL secret; skipped when unset)

Runner is pluggable via the RUNNER env (repo variable AGENT_BATCH_RUNNER):
  opencode (default) | claude-code | codex
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ["GITHUB_REPOSITORY"]
EVENT_NAME = os.environ["GITHUB_EVENT_NAME"]
DEFAULT_BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
RUNNER = (os.environ.get("RUNNER") or "opencode").strip().lower()
MODEL = (os.environ.get("MODEL") or "").strip()
PM_PANEL_URL = (os.environ.get("PM_PANEL_URL") or "").strip().rstrip("/")
BOT_NAME = "agent-batch[bot]"
BOT_EMAIL = "agent-batch[bot]@users.noreply.github.com"

with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as f:
    EVENT = json.load(f)

ISSUE = EVENT["issue"]
N = ISSUE["number"]
ISSUE_URL = ISSUE.get("html_url", f"https://github.com/{REPO}/issues/{N}")

if EVENT_NAME == "issue_comment":
    PROMPT = (EVENT["comment"].get("body") or "").strip()
    TRIGGER = EVENT["comment"]["user"]["login"]
else:
    PROMPT = f"{ISSUE.get('title', '')}\n\n{ISSUE.get('body') or ''}".strip()
    TRIGGER = ISSUE["user"]["login"]

STATE_DIR = "state/issues"
SESSION_FILE = f"{STATE_DIR}/{N}.json"
BRANCH = f"agent/issue-{N}"


def log(msg: str) -> None:
    print(f"[agent] {msg}", flush=True)


def run(cmd: list[str], timeout: int = 600, check: bool = False) -> subprocess.CompletedProcess:
    """Run a command, capture output, never raise unless check=True."""
    log("$ " + " ".join(cmd[:4]) + (" …" if len(cmd) > 4 else ""))
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=check
        )
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(cmd, 124, (e.stdout or ""), (e.stderr or ""))


def gh(*args: str) -> subprocess.CompletedProcess:
    return run(["gh", *args], timeout=120)


def gh_api(method: str, path: str, fields: list[str] | None = None) -> subprocess.CompletedProcess:
    cmd = ["gh", "api", "-X", method, f"repos/{REPO}{path}"]
    if fields:
        cmd += fields
    return run(cmd, timeout=120)


# ---------------------------------------------------------------- reactions
def add_reaction(content: str) -> tuple[str | None, str | None]:
    """Add a reaction; returns (reaction_id, target_path) for later removal."""
    if EVENT_NAME == "issue_comment":
        target = f"/issues/comments/{EVENT['comment']['id']}/reactions"
    else:
        target = f"/issues/{N}/reactions"
    r = gh_api("POST", target, ["-f", f"content={content}", "--jq", ".id"])
    return (r.stdout.strip() or None, target) if r.returncode == 0 else (None, target)


def remove_reaction(reaction_id: str | None, target: str | None) -> None:
    if not reaction_id or not target:
        return
    gh_api("DELETE", f"{target}/{reaction_id}")  # best-effort


# ---------------------------------------------------------------- git
def git_setup() -> None:
    run(["git", "config", "user.name", BOT_NAME])
    run(["git", "config", "user.email", BOT_EMAIL])


def checkout_agent_branch() -> None:
    """Check out agent/issue-<N> if it exists, else fork it from the default branch."""
    r = run(["git", "rev-parse", "--verify", f"origin/{BRANCH}"])
    if r.returncode == 0:
        run(["git", "checkout", "-B", BRANCH, f"origin/{BRANCH}"])
        log(f"resumed existing branch {BRANCH}")
    else:
        run(["git", "checkout", DEFAULT_BRANCH])
        run(["git", "checkout", "-b", BRANCH])
        log(f"created branch {BRANCH} from {DEFAULT_BRANCH}")


def push_with_retry() -> bool:
    for attempt in range(1, 4):
        r = run(["git", "push", "-u", "origin", BRANCH], timeout=180)
        if r.returncode == 0:
            return True
        log(f"push failed (attempt {attempt}/3), rebasing…")
        run(["git", "pull", "--rebase", "origin", BRANCH], timeout=180)
    return False


# ---------------------------------------------------------------- session state
def load_history() -> list[dict]:
    if os.path.exists(SESSION_FILE):
        try:
            return json.load(open(SESSION_FILE, encoding="utf-8")).get("history", [])
        except Exception:
            pass
    return []


def save_history(history: list[dict]) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SESSION_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"issue_number": N, "history": history, "updated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds")},
            f, ensure_ascii=False, indent=2,
        )


def build_prompt(history: list[dict]) -> str:
    """Compact prior turns + the new instruction, runner-agnostic."""
    ctx = (
        f"You are an autonomous coding agent working on issue #{N} of {REPO} "
        f"(triggered by {TRIGGER}).\n"
        "Rules:\n"
        "- Make the changes the issue asks for; keep the diff scoped to the issue.\n"
        "- The git repo is already checked out on your own branch; commit your work.\n"
        "- Never commit secrets, credentials, or API keys.\n"
        "- Your FINAL message is what gets posted back as an issue comment — write a "
        "clear summary of what you changed, test results, and anything the reporter "
        "should know.\n"
        "- If the request is unclear or out of scope, say so honestly in the final "
        "message instead of guessing.\n\n"
    )
    for turn in history[-8:]:
        role = "User (issue thread)" if turn["role"] == "user" else "Assistant (your previous reply)"
        ctx += f"--- {role} ---\n{turn['content'][:4000]}\n\n"
    ctx += f"--- NEW INSTRUCTION (issue #{N}) ---\n{PROMPT}\n"
    return ctx


# ---------------------------------------------------------------- runner
def install_runner() -> None:
    pkgs = {
        "opencode": "opencode-ai",
        "claude-code": "@anthropic-ai/claude-code",
        "codex": "@openai/codex",
    }
    if RUNNER not in pkgs:
        raise SystemExit(f"unknown RUNNER '{RUNNER}' — use opencode | claude-code | codex")
    r = run(["npm", "install", "-g", pkgs[RUNNER]], timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"failed to install {pkgs[RUNNER]}: {r.stderr[-500:]}")


def _extract_opencode_answer(stdout: str) -> str:
    """Last assistant text from `opencode run --format json` output (gitclaw-style)."""
    parts: list[str] = []
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") not in ("message", "message_end"):
            continue
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, list):
            parts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        elif isinstance(content, str):
            parts = [content]
        if parts:
            return "".join(parts).strip()
    return ""


def run_agent(prompt: str) -> str:
    """Run the chosen agent; returns its final answer text."""
    if RUNNER == "opencode":
        cmd = ["opencode", "run", "--format", "json", prompt]
        if MODEL:
            cmd += ["--model", MODEL]
        r = run(cmd, timeout=1500)
        answer = _extract_opencode_answer(r.stdout)
        if not answer:
            answer = r.stdout.strip()[-8000:]
    elif RUNNER == "claude-code":
        cmd = ["claude", "-p", prompt, "--output-format", "text", "--dangerously-skip-permissions"]
        if MODEL:
            cmd += ["--model", MODEL]
        r = run(cmd, timeout=1500)
        answer = r.stdout.strip()[-8000:]
    else:  # codex
        cmd = ["codex", "exec", "--full-auto", prompt]
        if MODEL:
            cmd += ["--model", MODEL]
        r = run(cmd, timeout=1500)
        answer = r.stdout.strip()[-8000:]
    if not answer:
        answer = "(agent produced no text output)"
    return answer


# ---------------------------------------------------------------- PR + comment
def ensure_pr() -> str | None:
    r = gh("pr", "list", "--head", BRANCH, "--json", "number,url", "--jq", ".[0]")
    if r.returncode == 0 and r.stdout.strip():
        try:
            return json.loads(r.stdout)["url"]
        except Exception:
            pass
    title = f"🤖 Agent: {ISSUE.get('title', '')}"[:72] or f"🤖 Agent work for issue #{N}"
    body = (f"Auto-generated by the hermes-agent-batch issue agent for **#{N}**.\n\n"
            f"Refs: {ISSUE_URL}\n\nReply to the issue to continue the session.")
    body_file = "/tmp/pr-body.md"
    with open(body_file, "w", encoding="utf-8") as f:
        f.write(body)
    r = gh("pr", "create", "--base", DEFAULT_BRANCH, "--head", BRANCH,
           "--title", title, "--body-file", body_file)
    return r.stdout.strip() or None


def comment_on_issue(text: str) -> None:
    body_file = "/tmp/issue-comment.md"
    with open(body_file, "w", encoding="utf-8") as f:
        f.write(text)
    gh("issue", "comment", str(N), "--body-file", body_file)


# ---------------------------------------------------------------- PM panel log
def log_to_panel(status: str, summary: str, pr_url: str = "") -> None:
    if not PM_PANEL_URL:
        log("PM_PANEL_URL unset — skipping panel log")
        return
    payload = {
        "project": REPO.split("/")[-1],
        "task_id": f"issue-{N}",
        "runner": RUNNER,
        "status": status,
        "pr_url": pr_url,
        "issue_url": ISSUE_URL,
        "summary": summary[:500],
    }
    req = urllib.request.Request(
        f"{PM_PANEL_URL}/api/agent-batch/log",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            log(f"panel log: HTTP {resp.status}")
    except (urllib.error.URLError, OSError) as e:
        log(f"panel log failed (non-fatal): {e}")


# ---------------------------------------------------------------- main
def main() -> int:
    log(f"event={EVENT_NAME} issue=#{N} runner={RUNNER} trigger={TRIGGER}")
    reaction_id, reaction_target = add_reaction("eyes")

    try:
        git_setup()
        checkout_agent_branch()
        history = load_history()
        log(f"history: {len(history)} messages ({'resume' if history else 'new session'})")

        install_runner()
        answer = run_agent(build_prompt(history))

        history.append({"role": "user", "content": PROMPT})
        history.append({"role": "assistant", "content": answer})
        save_history(history)

        run(["git", "add", "-A"])
        if run(["git", "diff", "--cached", "--quiet"]).returncode != 0:
            r = run(["git", "commit", "-m", f"agent: work on issue #{N}"], timeout=120)
            if r.returncode != 0:
                log(f"commit failed: {r.stderr[-500:]}")
        if not push_with_retry():
            log("push failed after 3 attempts — PR/comment still attempted")

        pr_url = ensure_pr()
        if pr_url:
            log(f"PR: {pr_url}")

        summary = answer[:3000]
        comment = (f"🤖 **Agent ({RUNNER})** finished working on this issue.\n\n{summary}\n")
        if pr_url:
            comment += f"\n\n📦 **PR:** {pr_url} (changes live on branch `{BRANCH}`)"
        comment += "\n\n> Reply to this issue to continue the same session."
        comment_on_issue(comment)

        log_to_panel("done", answer, pr_url or "")
        remove_reaction(reaction_id, reaction_target)
        add_reaction("+1")
        log("done ✓")
        return 0
    except Exception as e:
        log(f"FAILED: {e}")
        try:
            comment_on_issue(f"🤖 **Agent ({RUNNER})** failed: `{e}`\n\nSee the Actions run log for details.")
            log_to_panel("failed", str(e)[:500])
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
