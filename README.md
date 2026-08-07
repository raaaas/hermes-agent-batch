# Agent Batch 🤖

**Parallel AI task orchestrator for GitHub** — a Hermes desktop plugin + GitHub
Actions workflow. Paste a task list at night, Hermes phases it by dependency,
dispatches one GitHub Action per phase, and each task runs as its own agent on
its own branch, ending in a PR.

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
│  agent N: branch agent-0N-xxx → opencode → commit → PR        │
└───────────────────────────────────────────────────────────────┘
```

## Why opencode?

- Single binary, installed with one `curl` — no venv, no Python on the runner.
- Runs non-interactively (`opencode run "task"`).
- Free model tier via zen relay (`opencode/mimo-v2.5-free`,
  `opencode/deepseek-v4-flash-free`, …) — zero LLM cost.
- GitHub-hosted runners are free for public repos.

## Repo layout

```
.github/workflows/agent-batch.yml   # the parallel agent workflow
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

## Model options (free tier)

| model | notes |
|---|---|
| `opencode/mimo-v2.5-free` | default |
| `opencode/deepseek-v4-flash-free` | fast, cheap |
| `opencode/claude-fable-5` | stronger |

## License

MIT
