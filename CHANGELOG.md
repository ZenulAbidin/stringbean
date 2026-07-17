# Changelog

## [Unreleased]

## [0.2.0] - 2026-07-17

### Added

- `gpt-5.6` `terra` and `luna` model variants are now available alongside `sol`, as `gpt56-terra-*` and
  `gpt56-luna-*` orchestrator/advisor/reviewer agents.
- Explicit pinned-model Claude agents (`claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5-20251001`,
  `claude-fable-5`) are now available alongside the existing stable-alias agents (`claude-opus`,
  `claude-sonnet`, `claude-haiku`, `claude-fable`), so configs can choose portability or a pinned model.
- Preset D now includes a `claude-fable` agent; Fable 5 was previously entirely absent from the preset's
  agent catalog.

### Fixed

- `gpt56-*` Codex orchestrator/advisor/reviewer agents now request the `gpt-5.6-sol` model instead of the
  bare `gpt-5.6`, which Codex rejects under a ChatGPT-account login ("The 'gpt-5.6' model is not supported
  when using Codex with a ChatGPT account").
- The Claude adapter's legacy-model repair table no longer reroutes `fable` / `claude-fable-5` requests to
  `sonnet`; Fable 5 agents now actually invoke Fable 5 instead of silently running Sonnet 5. It also no
  longer rewrites the real model ids `claude-opus-4-8` and `claude-sonnet-5` down to their generic aliases,
  since the Claude Code CLI accepts full model names natively; only the malformed legacy string `opus-4.8`
  is still repaired.
- Explicit Codex `$sbx` runs now use a bundled, versioned local plugin tool instead of an escalated shell
  command, avoiding the over-broad provider-transfer Auto-review denial. The tool is workspace- and
  thread-bound, accepts only typed options, returns compact sanitized progress, and leaves
  polling/cancellation under normal policy. Codex keeps the skill model-visible so the established
  unqualified `$sbx` spelling resolves across repositories, while the skill instructions retain an
  explicit-request gate around the provider-launch tool.
- Built-in credential exclusions can no longer be negated by `.stringbeanignore` or configured
  project rules. Claude and Grok provider-transfer skills remain explicit-only at the host metadata
  layer; Codex enforces the same intent in the skill and tool descriptions so `$sbx` stays discoverable.
- Claude's plugin wrapper now preserves literal flag-like text inside the task argument instead of
  reclassifying it as Stringbean options; task text and flags remain separate argv entries.
- Source wrappers reject prerelease Python interpreters and rebuild mismatched managed runtimes as
  isolated, dependency-complete virtual environments using the highest available final Python 3.10+.

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
- Improved readability in adapter command construction, run persistence helpers, workflow status parsing, and structured output formatting without changing public artifact schemas.

### Fixed

- Clearer dependency-aware launcher resolution for Python runtime and project modules.
- Better CLI availability checks for local adapter executables.
- Placeholder `cat` fallback agents are rejected before real runs.
- Provider output streaming is sanitized and formatted for terminal/Codex consumption.
- Generated local probe state and fake implementer artifacts are no longer tracked.
- Release checklist again includes shipped plugin manifests, and release metadata now stays synchronized across package and plugin artifacts.
- Plain directories are now first-class workspaces: Git is optional by default, provider context stays scoped to the invocation directory, and Codex's non-Git execution path remains enabled.
- Sensitive path patterns, escaping symlinks, and nested repositories are excluded from provider context and protected by the Linux subprocess policy; excluded access is skipped without policy retries.
- Provider calls no longer inherit a 20-minute wall-clock kill timer. Watchdog thresholds now request explicit human approval and keep the agent alive whenever approval is declined, missing, or unavailable.
- Preset C now generates real Grok Build agents at low, medium, and high reasoning levels instead of unusable `cat` placeholder agents.
- Agent-plugin runs now emit every provider-process launch plus five-second keepalives, and Grok calls stream safely without exposing hidden thought events.
- Claude calls now use noninteractive stream JSON with immediate pipe forwarding, compact tool/result formatting, and valid full model IDs.
- Claude Code's plugin now defaults to compact live output so raw prompts do not overwhelm its tool result, while preserving a zero-exit sentinel on workflow failure.
- Claude Code's skill now separates task text from flags and waits on background task output instead of ending its turn before the final sentinel.
- Explicit per-role agent choices now take precedence over automatic mode selection.
- Claude Code plugin runs now use the native `Monitor` tool so intermediate Stringbean lines remain visible throughout long runs, with an incremental background-task fallback where Monitor is unavailable.
- Read-only Linux agents now have workspace mutations denied before they happen; ignored build trees use metadata-only baseline checks instead of eagerly hashing file contents.
- Claude presets now use the supported `opus`, `sonnet`, and `haiku` aliases with appropriate effort levels, while legacy pinned or invalid model names are normalized at runtime.

## 0.0.1 - 2026-07-11

### Added

- First working multi-agent orchestration prototype.
