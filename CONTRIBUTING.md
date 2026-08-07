# Contributing to Agent Batch

Thanks for your interest in contributing! Here's how to get started.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/<your-username>/agent-batch.git`
3. Create a branch: `git checkout -b feature/your-feature`
4. Make your changes
5. Submit a pull request

## Development

### Workflow file
The core GitHub Actions workflow is at `.github/workflows/agent-batch.yml`. Test changes by dispatching the workflow on your fork.

### Plugin
The Hermes plugin lives in `plugin/`. To test locally:

```bash
cp -r plugin/ ~/.hermes/plugins/agent-batch/
cp plugin/desktop/plugin.js ~/.hermes/desktop-plugins/agent-batch/
hermes plugins enable agent-batch
hermes gateway restart
```

### Orchestrator
`orchestrator.py` handles task phasing logic. Run it directly to test dependency analysis.

## Pull Requests

- Keep PRs focused on a single change
- Describe what changed and why
- Reference any related issues
- Test your changes before submitting

## Reporting Issues

Open an issue with:
- A clear description of the problem
- Steps to reproduce
- Expected vs actual behavior

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
