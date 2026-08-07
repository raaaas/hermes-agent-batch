# Changelog

## v0.1.0

Initial release.

### Added
- GitHub Actions workflow (`agent-batch.yml`) for running parallel AI agents on separate branches, each producing a PR.
- Hermes desktop plugin (`plugin/`) with FastAPI backend for planning, dispatching, and tracking agent runs.
- CLI orchestrator (`orchestrator.py`) for plan, dispatch, and status commands via GitHub API.
- Support for free-tier models via zen relay (`opencode/mimo-v2.5-free`, `opencode/deepseek-v4-flash-free`, `opencode/claude-fable-5`).
- Setup documentation (`docs/setup.md`).
