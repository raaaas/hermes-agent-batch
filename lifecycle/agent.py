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
    # The ORIGINAL issue is always part of the task — even when this run was
    # triggered by a comment (e.g. "retry") with no session history. Without
    # it the agent mistakes the comment for the whole task.
    issue_ctx = (
        f"ISSUE #{N} (original task): {ISSUE.get('title', '')}\n"
        f"{ISSUE.get('body') or ''}\n\n"
    )
    ctx = (
        f"You are an autonomous coding agent working on issue #{N} of {REPO} "
        f"(triggered by {TRIGGER}).\n"
        "Rules:\n"
        "- Make the changes the issue asks for; keep the diff scoped to the issue.\n"
        "- The git repo is already checked out on your own branch; commit your work.\n"
        "- Never commit secrets, credentials, or API keys.\n"
        "- **DO NOT install packages, run pip/conda/npm, or start servers.** The runner "
        "has no dependencies and verification is done by an external orchestrator. "
        "Write the code (and tests if the issue asks), then leave verification to the "
        "reviewer.\n"
        "- **IGNORE the agent harness**: the repo contains automation files "
        "(.github/workflows/, lifecycle/, state/) that RUN you — they are NOT part of "
        "the project. Never read, edit, or investigate them, and never investigate git "
        "history, past issue comments, or the Actions setup. Only the issue text above "
        "defines your task.\n"
        "- Prefer reading files and editing them directly over running shell commands "
        "that need packages.\n"
        "- Your FINAL message is what gets posted back as an issue comment — write a "
        "clear summary of what you changed, test results, and anything the reporter "
        "should know.\n"
        "- If the request is unclear or out of scope, say so honestly in the final "
        "message instead of guessing.\n\n"
    )
    ctx += issue_ctx
    for turn in history[-8:]:
        role = "User (issue thread)" if turn["role"] == "user" else "Assistant (your previous reply)"
        ctx += f"--- {role} ---\n{turn['content'][:4000]}\n\n"
    ctx += f"--- NEW INSTRUCTION (issue #{N}) ---\n{PROMPT}\n"
    return ctx


# ---------------------------------------------------------------- runner
def install_runner() -> None:
    pkgs = {
        "opencode": "opencode-ai@1.18.12",  # pinned: validated against kilo keyless direct (1.18.12); latest npm can hang/change json output
        "claude-code": "@anthropic-ai/claude-code",
        "codex": "@openai/codex",
    }
    if RUNNER not in pkgs:
        raise SystemExit(f"unknown RUNNER '{RUNNER}' — use opencode | claude-code | codex")
    r = run(["npm", "install", "-g", pkgs[RUNNER]], timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"failed to install {pkgs[RUNNER]}: {r.stderr[-500:]}")


def _extract_opencode_answer(stdout) -> str:
    """Last assistant TEXT from `opencode run --format json` output.

    opencode 1.18.x emits lines with types: step_start, step_finish, text,
    tool_use. Assistant words live in {"type":"text","part":{"text": ...}}.
    Walk ALL lines, keep the last non-empty text (the final message).
    Defensive: stdout can be None (killed subprocess) or bytes.
    """
    if stdout is None:
        return ""
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    last_text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("type") != "text":
            continue
        part = obj.get("part") or {}
        text = str(part.get("text", "")).strip()
        if text:
            last_text = text
    return last_text


def _is_usable_answer(text: str) -> bool:
    """Reject raw opencode event dumps that leak into the answer slot."""
    if not text or len(text) < 5:
        return False
    if text.startswith("{") or '"type":"' in text:
        return False
    return True


def run_agent(prompt: str) -> str:
    """Run the chosen agent; returns its final answer text."""
    if RUNNER == "opencode":
        # --auto: required, otherwise opencode auto-REJECTS every file write
        # in non-interactive mode ("The user rejected permission to use this
        # specific tool call") and the run exits 0 with NOTHING created.
        # --print-logs: opencode internals go to stderr, surfaced below when
        # the run produces no usable answer.
        cmd = ["opencode", "run", "--auto", "--print-logs", "--format", "json", prompt]
        if MODEL:
            cmd += ["--model", MODEL]
        r = run(cmd, timeout=6000)  # agent tasks with free kilo models are slow; step allows ~140min
        answer = _extract_opencode_answer(r.stdout)
        if not answer:
            tail = r.stdout or ""
            if isinstance(tail, bytes):
                tail = tail.decode("utf-8", errors="replace")
            answer = tail[-8000:]
        if not _is_usable_answer(answer):
            err_tail = (r.stderr or "") if isinstance(r.stderr, str) else str(r.stderr or "")
            log(f"opencode exit={r.returncode}, stderr tail: {err_tail[-1500:]}")
            answer = "(agent produced no usable text output — see Actions run log)"
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
