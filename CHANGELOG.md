# Changelog

## [Unreleased]

### Fixed

- Plain directories are now first-class workspaces: Git is optional by default, provider context stays scoped to the invocation directory, and Codex's non-Git execution path remains enabled.
- Sensitive path patterns, escaping symlinks, and nested repositories are excluded from provider context and protected by the Linux subprocess policy; excluded access is skipped without policy retries.
- Provider calls no longer inherit a 20-minute wall-clock kill timer. Watchdog thresholds now request explicit human approval and keep the agent alive whenever approval is declined, missing, or unavailable.
- Preset C now generates real Grok Build agents at low, medium, and high reasoning levels instead of unusable `cat` placeholder agents.
- Agent-plugin runs now emit every provider-process launch plus five-second keepalives, and Grok calls stream safely without exposing hidden thought events.
- Claude calls now use noninteractive stream JSON with immediate pipe forwarding, compact tool/result formatting, and valid full model IDs.
- Claude Code's plugin now defaults to compact live output so raw prompts do not overwhelm its tool result, while preserving a zero-exit sentinel on workflow failure.
- Claude Code's skill now separates task text from flags and waits on background task output instead of ending its turn before the final sentinel.
- Explicit per-role agent choices now take precedence over automatic mode selection.

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
- Claude Code plugin skill for invoking `sbx` as `/sbx` inside Claude Code.
- GitHub Actions CI for tests on Python 3.10, 3.11, and 3.12 plus package build checks.
- Release, contribution, security, and social launch documentation.

### Changed

- Python target set to support Python 3.10+ where dependencies are available.
- User-facing invocation names are limited to `stringbean` and `sbx`.
- Default execution profile is `rw`; `ro` remains available as create-only/read-only behavior.
- Codex-final output now emits explicit intermediate and final sentinel blocks for Codex UI integration.
- Grok agents now use Grok Build's headless argv/file prompt transports instead of stdin.
- Plugin wrappers now use full visible output mode by default; compact `--codex-final` remains available.
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
