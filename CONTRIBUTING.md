# Contributing to stringbean

Thanks for checking this out. stringbean is intentionally small and practical, so most changes should stay in the same spirit: minimal, explicit, and easy to inspect.

## What to work on

- CLI ergonomics (`src/agent_relay/cli.py`)
- Adapters (`src/agent_relay/adapters/`)
- Workflow engine and persistence (`src/agent_relay/workflow.py`, `src/agent_relay/state.py`)
- Tests (`tests/`)
- Documentation and release assets

## Setup

```bash
python3.10 -m pip install -e .[dev]
```

> If you use Python 3.11 and do not have dependencies installed there, run `python3.10` commands instead.

## Development checks

Run the full test suite before opening a PR:

```bash
python3.10 -m pytest -q
```

When you touch CLI behavior, add/adjust tests in `tests/` for:

- new commands/options
- mode behavior
- recovery and resume paths
- failure handling

## Coding style

Keep changes explicit and small:

- No `shell=True` in subprocess calls.
- Keep provider-specific logic inside adapters.
- Prefer filesystem artifacts over in-memory-only behavior.
- Use Pydantic models for schema-shaped data.
- Document behavior with tests and keep behavior deterministic.

## Release readiness checks

Before a release, include:

1. Tests passing.
2. `README.md` updated for any command/interface changes.
3. `CHANGELOG.md` entry.
4. Release checklist in `RELEASE.md` completed.

## Reporting

Open an issue or start a discussion for API changes before implementation, especially for:

- command contract changes
- config schema changes
- changes to resume/state persistence
