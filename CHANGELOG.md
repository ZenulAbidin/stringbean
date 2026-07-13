# Changelog

## [Unreleased]

- Nothing yet.

## [0.1.0] - 2026-07-13

### Added

- Local multi-agent workflow orchestration across Codex, Claude, Grok, and generic adapters.
- Filesystem-first run persistence (`.stringbean/runs/...`) with resumable workflows.
- Mode-aware role selection (`auto|high|medium|low`) with per-role overrides.
- Auto mode model catalog/rationale in dry runs.
- CLI commands:
  - `stringbean init`
  - `stringbean doctor`
  - `stringbean agents`
  - `stringbean run`
  - `stringbean resume`
  - `stringbean status`
  - `stringbean logs`
- Slash-style convenience launcher:
  - installed `sbx` console script
  - `scripts/sbx` source-checkout wrapper for self-bootstrapping local use
- Codex plugin skill and custom-prompt fallback for invoking `sbx` inside Codex.
- Grok Build plugin skill for invoking `sbx` as `/sbx` inside Grok.
- GitHub Actions CI for tests on Python 3.10, 3.11, and 3.12 plus package build checks.
- Release, contribution, security, and social launch documentation.

### Changed

- Python target set to support Python 3.10+ where dependencies are available.
- User-facing invocation names are limited to `stringbean` and `sbx`.
- Default execution profile is `rw`; `ro` remains available as create-only/read-only behavior.
- Codex-final output now emits explicit intermediate and final sentinel blocks for Codex UI integration.
- Grok agents now use Grok Build's headless argv/file prompt transports instead of stdin.
- Local wrapper shims reduce PATH/global environment mismatch issues.

### Fixed

- Clearer dependency-aware launcher resolution for Python runtime and project modules.
- Better CLI availability checks for local adapter executables.
- Placeholder `cat` fallback agents are rejected before real runs.
- Provider output streaming is sanitized and formatted for terminal/Codex consumption.
- Generated local probe state and fake implementer artifacts are no longer tracked.

## 0.0.1 - 2026-07-11

### Added

- First working multi-agent orchestration prototype.
