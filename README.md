# Agent Batch 🤖

▶️ **[![demo preview](docs/hermes-agent-batch-demo.gif)](docs/hermes-agent-batch.mp4)**

Issue opened → agent runs in its own GitHub Actions sandbox → branch + PR + comment.
**[Watch the full 20s demo](docs/hermes-agent-batch.mp4)** — GitHub plays it
on the file page (READMEs can't host video players natively, only previews).

**An overnight squad of AI coding agents that runs entirely inside GitHub
Actions.** Drop a task list before bed: every task gets its own **fresh, fully
isolated Linux sandbox VM**, its own git branch, and its own coding agent.
They work in parallel while you sleep; you wake up to clean PRs.

- **Full sandbox per task** — GitHub-hosted runners are throwaway VMs. An
  agent can't touch your laptop, your prod, or its siblings' work.
- **Free** — public repos get free GitHub Actions minutes; several runners
  (kilo, opencode free models, cline) cost $0 in LLM credits.
- **Better than "the internet" of babysitting one agent** — no long shared
  context rotting over thousands of requests, no merge fights, no stuck
  terminal. Each agent starts with a clean checkout and full project context.

## 🫡 The runner squad

Issue-agent brains, pluggable via `AGENT_BATCH_RUNNER`. Pick a free one or bring your own key:

[![cline — default](https://img.shields.io/badge/cline-default%20brain-9B5DE5?style=flat-square&logo=cline&logoColor=white)](#setup)
[![opencode](https://img.shields.io/badge/opencode-free+tier-111111?style=flat-square&logo=opencode&logoColor=white)](#setup)
[![kilo](https://img.shields.io/npm/v/%40kilocode%2Fcli?label=kilo%20CLI&logo=npm&style=flat-square)](https://www.npmjs.com/package/@kilocode/cli)
[![claude-code](https://img.shields.io/badge/claude--code-BYOK-D97757?style=flat-square&logo=anthropic&logoColor=white)](#setup)
[![codex](https://img.shields.io/npm/v/%40openai%2Fcodex?label=codex&style=flat-square)](https://www.npmjs.com/package/@openai/codex)
[![dsh](https://img.shields.io/badge/dsh-deepseek%20harness-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](#setup)
[![qoder](https://img.shields.io/badge/qodercli-soon%20%3A%29-111111?style=flat-square)](#setup)

| Runner        | Free path                          | BYOK path                                    |
| ------------- | ---------------------------------- | -------------------------------------------- |
| **cline** ⭐  | Cline Credits gateway              | `AGENT_BATCH_PROVIDER` + provider key secret |
| opencode      | zen relay free models              | `OPENCODE_API_KEY`                           |
| kilo          | — (CLI default models need a Kilo "Go" sub) | Kilo account login               |
| claude-code   | —                                  | `ANTHROPIC_API_KEY`                          |
| codex         | —                                  | `OPENROUTER_API_KEY` (or any codex provider) |
| dsh           | —                                  | `DEEPSEEK_API_KEY` (+ optional proxy URL)    |
| qoder         | 🔜 pending headless CI auth         | —                                            |

**What "parallel" actually means:** 10 tasks → 10 GitHub Actions jobs → 10
VMs → 10 branches → 10 PRs. They run at the same time on the *same repo*
without colliding, because each one only ever sees its own copy and only
touches its own branch. One agent doing 10 tasks sequentially shares a single
degrading context window; ten agents each get a fresh one.

The core insight: **parallel agents on separate branches push a project
forward with better quality than one agent hammering a single branch with
thousands of requests.** Each agent gets a clean checkout, full project
context, and no merge conflicts with its siblings — until the PRs land.

```
┌─ Hermes desktop (operator console) ──────────────────────────┐
│  tasks in → Hermes phases them → dispatch phase N → track PRs │
└──────────────┬────────────────────────────────────────────────┘
               │ GitHub Actions
               ▼
┌─ workflow: agent-batch.yml ──────────────────────────────────┐
│  prepare: task list → matrix                                  │
│  agent N: branch agent-0N-xxx → $RUNNER → commit → PR         │
└───────────────────────────────────────────────────────────────┘
```

## Why GitHub Actions is the whole backend

- **The workflow *is* the infrastructure** — one YAML file, no server, no
  queue, no Docker. Push the file, open an issue, agents run.
- **Free compute** — public repos get unlimited GitHub-hosted runner minutes;
  pair them with the free-model runners above and the whole pipeline costs $0.
- **Real sandboxing** — every job is a clean, networked, disposable VM that
  vanishes after the run. Failed agent? Its sandbox is already gone; retry on
  a fresh one.
- **Matrix fan-out for free** — GitHub's `strategy.matrix` spawns N parallel
  jobs natively, with logs, timing, and retry per job.

## Repo layout

```
.github/workflows/agent-batch.yml   # the parallel agent workflow (nightly/on-demand batch)
.github/workflows/agent-issues.yml  # the gitclaw-style issue agent (issue → branch → PR)
lifecycle/agent.py                  # issue-agent brain: session memory, runner, PR, comment, panel log
plugin/
  plugin.yaml                       # hermes plugin manifest (enable gate)
  dashboard/manifest.json           # backend manifest
  dashboard/plugin_api.py           # FastAPI router: plan/dispatch/runs/status
  desktop/plugin.js                 # desktop UI (operator console)
docs/setup.md                       # step-by-step installation
```

## Quick start

1. **Workflow** — this repo already ships it; for another repo, copy
   `.github/workflows/agent-batch.yml` and add the secret `OPENCODE_API_KEY`
   (your zen relay key).
2. **Plugin** — copy `plugin/` to `~/.hermes/plugins/agent-batch/` and
   `plugin/desktop/plugin.js` to `~/.hermes/desktop-plugins/agent-batch/`,
   then:
   ```bash
   hermes plugins enable agent-batch
   hermes gateway restart          # mount the backend
   ```
   In the desktop app: ⌘K → **Reload desktop plugins** → open **Agent Batch**
   from the sidebar.
3. **Use** — paste tasks, save, ask Hermes to phase them, then hit **Dispatch**
   per phase. Review the PRs and merge.

## The orchestration loop (Hermes agent)

1. **Collect** — tasks land in the plugin (or chat).
2. **Phase** — Hermes analyzes dependencies: independent tasks share a phase
   (run in parallel), dependent tasks wait for the next phase.
3. **Context** — project memory / prior phase results are passed into each
   workflow dispatch so every agent starts informed.
4. **Dispatch** — one `workflow_dispatch` per phase; matrix fans out to N
   parallel jobs, each on branch `agent-NN-xxxx`.
5. **Track** — plugin polls GitHub: workflow runs + open PRs.
6. **Next phase** — once a phase's PRs are reviewed/merged, the next phase
   dispatches with updated context.

## Issue-driven mode (gitclaw-style) — open an issue, get a PR

Modeled on [SawyerHood/gitclaw](https://github.com/SawyerHood/gitclaw): the repo
runs its own issue agent with no servers, no extra infra — just GitHub Issues +
Actions.

- **Open an issue** → the agent starts, works on branch `agent/issue-<N>`,
  opens a PR, and replies as an issue comment with a summary + PR link.
- **Comment on the issue** → the agent **resumes the same session**: the
  conversation lives in `state/issues/<N>.json`, committed to git, so every
  comment continues where the last run left off (long-term memory).
- 👀 while working, ✅ when done. Bot comments never trigger.
- **Security**: only repo OWNER / MEMBER / COLLABORATOR can trigger. Public
  repo = the issue thread (and its state) is public — use a private repo for
  private work.

### Setup

1. Copy `.github/workflows/agent-issues.yml` + the `lifecycle/` folder into the
   target repo (this repo already ships both).
2. Add the model/runner secret your agent needs:
   - cline → nothing for Cline's own gateway (spends Cline Credits); for
     free/BYOK models set `AGENT_BATCH_PROVIDER` + the matching key:
     `openrouter` → `OPENROUTER_API_KEY`, `anthropic` → `ANTHROPIC_API_KEY`,
     `openai` → `OPENAI_API_KEY`, …
   - opencode → `OPENCODE_API_KEY`
   - kilo → a Kilo account with an active "Go" subscription — keyless CI runs
     fail on the model call (verified in Actions run 37748996133)
   - claude-code → `ANTHROPIC_API_KEY`
   - codex → `OPENROUTER_API_KEY` (or any provider key codex supports)
   - dsh → `DEEPSEEK_API_KEY` (+ optional `DEEPSEEK_BASE_URL`)
3. Optional repo variables:
   - `AGENT_BATCH_RUNNER` — `cline` (default) | `opencode` | `kilo` | `claude-code` | `codex` | `dsh`
   - `AGENT_BATCH_MODEL` — e.g. `qwen/qwen3.7-flash` (cline) or
     `opencode/mimo-v2.5-free` (opencode); empty = the runner's own default
   - `AGENT_BATCH_PROVIDER` — cline BYOK provider id (`openrouter`,
     `anthropic`, `openai`, …); unset = Cline's own gateway
   - (qoder-cli is intentionally not wired yet — GitHub-hosted runners can't
     browser-login to Qoder; revisit when token auth lands)
4. **Per-issue CLI (the multi-launcher bit):** write `runner: opencode` — or
   `cline` / `kilo` / `claude-code` / `codex` / `dsh` — anywhere in the issue
   title or body (or a resume comment) and that issue's agent runs with that
   CLI. No marker → `AGENT_BATCH_RUNNER` variable → cline.
5. For the **`dsh`** runner (DeepSeek Harness), add the `DEEPSEEK_API_KEY`
   secret (required) and optionally `DEEPSEEK_BASE_URL` (an OpenAI-compatible
   proxy endpoint). `dsh --profile headless "task"` prints the final answer and
   exits — no server, CI-safe.
6. Optional: `PM_PANEL_URL` secret (e.g. `https://panel.example.com`) — every
   run is POSTed to `<url>/api/agent-batch/log` so the Hermes project-manager
   panel (🤖 Agent Batch view) shows what the GitHub side did. Skipped silently
   when unset.

### How it works

```
issue opened / comment created
  → guard: owner/member/collaborator only
  → 👀 reaction
  → checkout agent/issue-<N> (resume) or fork from default branch (new)
  → load state/issues/<N>.json (prior turns)
  → run $RUNNER with history + new instruction
  → append turn to state/issues/<N>.json, commit everything, push
  → open PR if none exists yet
  → comment on the issue (summary + PR link) + ✅ reaction
  → POST run log to PM_PANEL_URL (optional)
```

The existing nightly batch (`agent-batch.yml`) is untouched — both modes can
run side by side: the batch dispatches N parallel agents on demand, the issue
agent reacts to GitHub issues one session at a time.

## Model options (free tier)

| model | notes |
|---|---|
| `opencode/mimo-v2.5-free` | default |
| `opencode/deepseek-v4-flash-free` | fast, cheap |
| `opencode/claude-fable-5` | stronger |

## License

MIT
