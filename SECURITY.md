# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability, please report it by opening a GitHub issue or contacting the maintainers directly. Do not disclose vulnerabilities publicly until a fix is available.

## Security Considerations

### Secrets and Credentials

- **Never commit `OPENCODE_API_KEY` or any API keys** to the repository. Use GitHub Actions secrets instead.
- Rotate keys immediately if they are accidentally exposed.
- Avoid logging secrets or passing them via environment variables that may leak in logs.

### GitHub Actions Workflow

- Agents run on GitHub-hosted runners with access to repository code. Be aware of the data each agent can read.
- The workflow dispatches tasks from an untrusted task list. Validate and sanitize task input before execution.
- Branch names are generated from task input; ensure they are sanitized to prevent injection.

### Plugin (Hermes Desktop)

- The plugin backend (`plugin_api.py`) exposes HTTP endpoints. Ensure it only listens on localhost or a trusted network.
- User-provided task lists are passed to AI agents. Be cautious of prompt injection — agents have write access to the repo via PRs.

### AI Agent Behavior

- Each agent creates branches and opens PRs. Review PRs before merging.
- Agents may execute arbitrary code as part of their tasks. Run them in isolated environments when possible.
- Limit the scope of repository permissions granted to the `GITHUB_TOKEN` used by the workflow.

### General

- Keep dependencies up to date.
- Use branch protection rules on `main`.
- Audit merged PRs periodically for unexpected changes.
