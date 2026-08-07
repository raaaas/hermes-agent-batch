# Setup

Step-by-step installation of the Agent Batch orchestrator.

## 1. GitHub Actions workflow (per repo)

Copy `.github/workflows/agent-batch.yml` into the target repo (or keep it in
this repo to dogfood). Then add one secret:

- **Settings → Secrets and variables → Actions → New repository secret**
  - Name: `OPENCODE_API_KEY`
  - Value: your zen relay API key (the one opencode uses locally)

> The workflow calls `opencode.ai/install` on the runner, so no repo-level
> dependencies are needed.

## 2. Hermes desktop plugin

### Backend

```bash
mkdir -p ~/.hermes/plugins/agent-batch
cp -r plugin/dashboard ~/.hermes/plugins/agent-batch/
cp plugin/plugin.yaml ~/.hermes/plugins/agent-batch/
hermes plugins enable agent-batch
```

The backend mounts at `/api/plugins/agent-batch/`. It reads the GitHub token
from `GITHUB_TOKEN` (env or `~/.hermes/.env`).

### Desktop UI

```bash
mkdir -p ~/.hermes/desktop-plugins/agent-batch
cp plugin/desktop/plugin.js ~/.hermes/desktop-plugins/agent-batch/
```

In the desktop app: **⌘K → Reload desktop plugins**, then open **Agent Batch**
from the sidebar.

> The Python backend only mounts at gateway/app startup — after enabling the
> plugin, restart the Hermes gateway once (or the desktop app's backend).
> Never restart the gateway from inside a chat session.

## 3. First run

1. Open **Agent Batch** in the desktop app.
2. Paste tasks (one per line), set the repo (`owner/repo`) and model.
3. **Save tasks**.
4. Ask Hermes: *"phase these tasks"* — it groups them by dependency into
   parallel phases and stores the plan.
5. Hit **Dispatch** on phase 1. Each task becomes a job on its own branch.
6. Review the PRs, merge, then dispatch phase 2 — Hermes passes the updated
   context automatically.

## 4. Manual dispatch (no UI)

```bash
curl -X POST https://api.github.com/repos/OWNER/REPO/actions/workflows/agent-batch.yml/dispatches \
  -H "Authorization: token $GITHUB_TOKEN" \
  -d '{"ref":"main","inputs":{"tasks":"task one\ntask two","model":"opencode/mimo-v2.5-free"}}'
```

## Troubleshooting

| symptom | fix |
|---|---|
| `404` on dispatch | workflow file not on the default branch, or token lacks `workflow` scope |
| runs fail at install | runner has no network to `opencode.ai` — use a self-hosted runner |
| agent makes no changes | task too vague — add acceptance criteria in the task text |
| plugin page empty | backend not mounted — check `hermes plugins list` and restart gateway |
