# Changelog

## [Unreleased]

- Initial public release docs and polish.
- Added release and distribution documentation.
- Added project license and contribution/security guidance.
- Added GitHub release checklist and social announcement copy for community launch.

## [0.1.0] - 2026-07-12

### Added

- Local multi-agent workflow orchestration across Codex, Claude, Grok, and generic adapters.
- Filesystem-first run persistence (`.stringbean/runs/...`) with resumable workflows.
- Mode-aware role selection (`auto|high|medium|low`) with per-role overrides.
- CLI commands:
  - `stringbean init`
  - `stringbean doctor`
  - `stringbean agents`
  - `stringbean run`
  - `stringbean resume`
  - `stringbean status`
  - `stringbean logs`
- Slash-style convenience launcher:
  - `scripts/sbx` for `stringbean sbx "task"` style local invocation
- Local wrapper shims to reduce PATH/global environment mismatch issues.

### Changed

- Python target set to support Python 3.10+ where dependencies are available.
- Internal package name remains `stringbean` while exposing `agent-relay` entrypoint for compatibility.

### Fixed

- Clearer dependency-aware launcher resolution for Python runtime and project modules.
- Better CLI availability checks for local adapter executables.

## 0.0.1 - 2026-07-11

### Added

- First working multi-agent orchestration prototype.
