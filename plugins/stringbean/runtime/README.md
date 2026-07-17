# stringbean

stringbean is a lightweight local orchestrator for coding-agent CLIs. It coordinates multiple
agents (Codex, Claude, Grok, or any generic CLI adapter) without replacing native interfaces, keeps a
filesystem audit trail, and runs a small resumable workflow.

It is designed to run headlessly and be called from your favorite agent program (codex, claude code, etc) using `/sbx`. It can be run in read-only mode or in read-write mode (the default).

Current release: `0.2.0`.

## What this project does

Stringbean is excellent for running complex tasks which require long durations. Each invocation runs several advisor and orchestrator agents while auto-selecting the most efficient model with a reasonable quota. This way, you can avoid running your entire session in Fable or Sol and burning through your usage limit. It is also great for turning terse instructions into specific, actionable plans.

- Runs an MCP-style workflow (planning → review → implementation → review) across configured agent roles
- Supports multiple providers through adapter plugins:
  - Codex (`codex`)
  - Claude Code (`claude`)
  - Grok Build (`grok`)
  - Generic CLI adapters (`generic`)
- Persists complete run artifacts: prompts, raw output, parsed output, metadata, state transitions, and event log
- Can be resumed after interruption using persisted state
- Enforces basic repository safety and read-only behavior checks
- Configurable models (e.g. `gpt-5.5`, `opus-4.8`) and reasoning levels

## What it does not do

Stringbean is not particularly good at running small tasks consisting of a single shell command, due to the amount of overhead required in running all of the subagents.

It also does not replace your model subscription or credentials.

## Architecture

```text
User
  │
  ▼
stringbean CLI
  │
  ├── workflow state machine
  ├── run persistence
  ├── permission checks
  └── subprocess adapters
         │
         ├── Codex CLI
         ├── Claude Code
         ├── Grok CLI
         └── Generic CLI
```

## Prerequisites

- A final, non-prerelease Python 3.10+ (Python 3.12+ recommended)
- Local install of desired provider CLIs (optional)
- Optional Git availability for enhanced status/diff checks; ordinary directories are first-class workspaces

## Install

From a checkout:

```bash
python3 -m pip install .

# Create local binaries and runtime so that it can run
# even if the active Python is missing dependencies
scripts/install-local-shims.sh


stringbean --version
sbx --help
```

The source wrappers ignore alpha, beta, and release-candidate interpreters. If the bundled
`.stringbean-runtime` uses a prerelease or a different preferred Python minor, Stringbean rebuilds
that managed runtime from the highest available final interpreter.

For editable development:

```bash
python3 -m pip install -e ".[dev]"
```

## Quick start (5 minutes)

```bash
stringbean init
stringbean doctor
stringbean run "Inspect lightweight input validation in API endpoint"
stringbean run "Implement lightweight input validation in API endpoint"
sbx "Inspect lightweight input validation in API endpoint"
stringbean status
stringbean logs <run-id>
```

For the lightweight slash-style flow from any directory, use the installed `sbx` command:

```bash
sbx "Inspect feature X"
sbx "Implement feature X" --mode auto
sbx "Refactor auth flow" --mode high
```

When running straight from a source checkout without installing the package, use the repo wrapper:

```bash
./scripts/sbx "Inspect feature X"
```

If `stringbean` or `sbx` resolves to an old global shim, refresh the local shims:

```bash
scripts/install-local-shims.sh
```

Use `--dry-run` to inspect what would happen before running:

```bash
stringbean run --dry-run "Implement auth checks" 
```

## CLI reference

- `stringbean init`
  - Creates `.stringbean/config.yaml` in the current project, or `~/.stringbean/config.yaml` if no local project config directory is discovered
  - Optional `--preset A|B|C|D` (default A)
  - `--templates` copies editable templates into `.stringbean/templates/`
  - `--force` overwrites existing config
- `stringbean doctor`
  - Validates runtime requirements, config, CLI availability, templates, state dir
  - Writes detected CLI capabilities to `.stringbean/cli-capabilities.json` (or `~/.stringbean/cli-capabilities.json` when using the home default)
- `stringbean agents`
  - Lists configured agents and executable availability
- `stringbean run TASK`
  - `--config PATH`
  - `--orchestrator`, `--advisor`, `--implementer`, `--reviewer`
  - `--mode auto|high|medium|low` (default `auto`)
  - `--orchestrator-mode`, `--advisor-mode`, `--implementer-mode`, `--reviewer-mode`
  - `--profile ro|rw`, `--ro`, `--rw`
    - `rw` is the default profile: write-capable agents may modify files in service of the task. On Linux, read-only roles are protected at file-open time by the Stringbean preload guard and then diff-checked as defense in depth.
    - `ro` is the create-only profile: subagents can inspect, run commands, and create new files/directories, but modifications, deletes, renames, moves, or type changes to pre-existing repository paths are treated as policy violations and rolled back where safe.
    - Codex agents are launched with explicit approval/sandbox flags instead of inherited defaults: `ro` uses workspace-write with Stringbean diff enforcement; `rw` uses danger-full-access at the provider layer while Stringbean still diff-checks read-only roles.
    - Subagents receive a Stringbean denylist for destructive commands such as `rm`, `sudo`, `dd`, `mkfs`, `shutdown`, and destructive git operations such as `git reset`, `git clean`, and `git push`.
  - `--policy-retries N`
    - Retries an agent call after ordinary filesystem policy violations by reframing the role prompt as analysis-only and naming the forbidden paths. Excluded/sensitive-path access is never retried. Default: `workflow.max_policy_violation_retries` (`2`).
  - `--max-review-rounds N`
  - `--no-advisor`
  - `--dry-run`
  - `--no-agent-stream` / `--no-agent-output` hides the live provider stdout/stderr stream. By default Stringbean shows selective formatted provider output: prompt echoes and CLI boilerplate are suppressed, visible escapes such as `\n` are decoded, structured JSON answers are collapsed into readable result lines, tool output bodies are capped at three visible lines, terminal output uses TTY-aware bold/white labels, and raw stdout/stderr are still retained in run artifacts.
  - `--codex-final` / `--plugin-final` emits compact intermediate status and sanitized agent-output lines prefixed with `STRINGBEAN_INTERMEDIATE:` plus a final block wrapped in `STRINGBEAN_FINAL_START` / `STRINGBEAN_FINAL_END`.
  - `--plugin-full-output` / `--full-output` emits normal visible output and raw live agent stdout/stderr, then appends the same final sentinel block for Codex/Grok/Claude plugin wrappers. It exits 0 after printing the final block even when the Stringbean result says `FAILED`, so the host plugin can still read and report the failure.
  - `--plugin-compact-output` / `--plugin-live-output` emits compact live progress and sanitized agent output plus the final sentinel block. It also exits 0 after reporting workflow failures, making it suitable for context-sensitive hosts such as Claude Code.
  - `--ignore-sandbox-warnings` is a diagnostic escape hatch: Stringbean records filesystem sandbox warnings but does not rollback/fail only because of those warnings. This can leave files modified.
  - `--quiet`
  - `--run-id`
- `stringbean resume RUN_ID`
  - Continue a partial run from persisted state
- `stringbean status [RUN_ID]`
  - Show run stage, status, errors, review rounds, run location
- `stringbean logs RUN_ID`
  - Show event log and call artifacts summary

### Installation alias for quick launch

- `sbx` is installed as a console script by `python3 -m pip install .`.
- `scripts/stringbean` and `scripts/sbx` are source-checkout shims that always route into repo code.

```bash
stringbean run "Quick task"
sbx "Quick task"
./scripts/sbx "Quick task from a source checkout"
```

### Codex quick command (slash-style)

You can run the local wrapper as a one-off command from the repo:

```bash
./scripts/sbx "Inspect validation in signup endpoint" --mode auto
./scripts/sbx "Fix validation in signup endpoint" --mode auto
./scripts/sbx "Refactor auth flow" --mode high
./scripts/sbx "Inspect without modifying existing files" --ro
```

If you want it as a slash command in your UI, map `/sbx` to run:

```text
./scripts/sbx "<TASK>" [--ro|--rw] [--mode auto|low|medium|high]
```

If you want `/sbx` usable directly from zsh terminal:

```bash
source /path/to/stringbean/scripts/sbx-zsh-hook.zsh
```

Then type:

```bash
/sbx "Fix login validation bug" --mode high
```

If `sbx` is still being resolved to an old global script, this hook also defines a `sbx` shell function so `sbx ...` always uses the repo-local wrapper.

### Codex plugin

For use inside Codex, prefer the local Stringbean plugin. It installs a `stringbean:sbx` skill that
starts a bundled, versioned Stringbean source snapshot through a local plugin tool, polls visible output during long
runs, and mirrors the `STRINGBEAN_FINAL_START` / `STRINGBEAN_FINAL_END` result into the visible
final answer.

The plugin's `start_sbx` tool is the only pre-approved operation. It runs as a local Codex plugin
process instead of an escalated model-issued shell command, so an explicit `$sbx` invocation can
use configured providers without the erroneous second “data transfer” denial. The tool accepts a
small typed flag set, derives the workspace from Codex's host-provided sandbox metadata, strips
workspace-owned `PATH` entries and runtime overrides, and executes the source snapshot bundled in
the installed plugin. Stringbean's mandatory credential exclusions and Linux file-open guard still
apply. Installing and enabling the plugin is the trust boundary for this local provider capability:
the skill remains model-visible so the established unqualified `$sbx` spelling can resolve, while
its instructions permit `start_sbx` only for an explicit `$sbx` / `stringbean:sbx` invocation or a
direct request to run Stringbean. This instruction-level gate is not cryptographic proof of a
particular prompt token; enabling the plugin grants access to the narrow typed tool.

The current trusted launcher targets Linux/FHS hosts with a final, dependency-complete Python 3.10+
at `/usr/bin/python3`. The installer validates that exact interpreter against the bundled source.
Pre-approved runs never install or upgrade packages. HOME-backed CLI sessions work by default;
custom XDG/Codex/Claude config locations and the standard OpenAI, Anthropic, and xAI/Grok API-key
variables are explicitly forwarded by the plugin.

Install or refresh it with:

```bash
scripts/install-codex-plugin.sh
```

Then restart Codex or open a new task and invoke:

```text
$sbx inspect whether README exists
$sbx fix typo in README --mode high
```

If Codex displays the plugin-qualified skill name, choose `stringbean:sbx`.

`--plugin-full-output` is intentionally verbose for agent-plugin diagnostics. `--plugin-compact-output` keeps live sanitized assistant/tool/status lines and the final sentinel without replaying raw prompts. Plugin runs emit an immediate command-accepted line and five-second heartbeats by default so Codex, Grok, and Claude do not mistake an active provider call for a stalled command. Use `--no-codex-progress` for fewer progress lines or `--codex-progress-interval N` to choose a different heartbeat interval.

Codex plugins are installed from this repo's local marketplace at `.agents/plugins/marketplace.json`.

### Grok Build plugin wrapper

For use inside Grok Build, install the local Grok plugin. It provides a user-invocable `sbx` skill for `/sbx ...` that runs `sbx --plugin-full-output`, surfaces visible run output, and mirrors the final sentinel result into Grok's visible answer.

Install or refresh it with:

```bash
scripts/install-grok-plugin.sh
```

Then restart Grok Build or open a new task and invoke:

```text
/sbx inspect whether README exists
/sbx fix typo in README --mode high
```

If Grok displays plugin-qualified skill names, choose `grok-stringbean:sbx`.

### Claude Code plugin wrapper

For use inside Claude Code, install the local Claude plugin. It provides a user-invoked `sbx` skill for `/sbx ...` that runs `sbx --plugin-compact-output` through Claude's `Monitor` tool, surfaces meaningful lines as they arrive instead of batching them in one Bash result, and mirrors the final sentinel result into Claude's visible answer. Deployments without `Monitor` fall back to a background task with incremental output-file reads.

Install or refresh it with:

```bash
scripts/install-claude-plugin.sh
```

Then restart Claude Code or open a new task and invoke:

```text
/sbx inspect whether README exists
/sbx fix typo in README --mode high
```

If Claude displays plugin-qualified skill names, choose `claude-stringbean:sbx`.

Claude plugins are installed from this repo's local marketplace at `.claude-plugin/marketplace.json`.

### Codex custom prompt wrapper

Stringbean also includes a legacy custom prompt that invokes the same `start_sbx` / `poll_sbx`
plugin tools, summarizes only `STRINGBEAN_INTERMEDIATE:` progress while the run is active, and
copies the `STRINGBEAN_FINAL_START` / `STRINGBEAN_FINAL_END` result into the visible final answer.
Its installer refreshes the Codex plugin first so it never falls back to a model-issued shell launch.

Custom prompts are a legacy slash-command fallback. Use the plugin skill above when available.

Install it with:

```bash
scripts/install-codex-prompts.sh
```

Then restart Codex or open a new task and run:

```text
/prompts:sbx inspect whether README exists
/prompts:sbx fix typo in README
```

## Release process

- Keep release notes current in [`CHANGELOG.md`](CHANGELOG.md).
- Read the step-by-step release checklist in [`RELEASE.md`](RELEASE.md).
- Hosting and publication expectations are documented in [`docs/hosting.md`](docs/hosting.md).
- Before announcing publicly, verify:
  - `python -m pytest -q` passes
  - `python -m build` and `python -m twine check dist/*` pass
  - `stringbean doctor` is green
  - `scripts/sbx` smoke test succeeds for a tiny task

## Social launch (X/Twitter)

Use [`docs/x_post.md`](docs/x_post.md) for polished announcement drafts you can post after release.

## Configuration reference

Configuration lives in `.stringbean/config.yaml` by default; if no local project directory is found, it uses `~/.stringbean/config.yaml`.

Minimal example:

```yaml
version: 1

agents:
  sol:
    adapter: codex
    model: gpt-5.6-sol
    role: orchestrator
    permissions: read_write
    command: null
    prompt_transport: stdin
    timeout_seconds: 0
    idle_timeout_seconds: 7200
    max_repeated_output_lines: 200

  sonnet:
    adapter: claude
    model: sonnet
    role: advisor
    permissions: read_only
    command: [claude, --model, sonnet, --effort, medium]
    mode: medium
    prompt_transport: stdin
    timeout_seconds: 0
    idle_timeout_seconds: 7200
    max_repeated_output_lines: 200

  grok:
    adapter: grok
    model: grok-build
    role: implementer
    permissions: read_write
    command:
      - grok
      - --model
      - grok-build
      - --reasoning-effort
      - high
    prompt_transport: argv

  sol-review:
    adapter: codex
    model: gpt-5.6-sol
    role: reviewer
    permissions: read_only
    command: null
    prompt_transport: stdin

workflow:
  orchestrator: sol
  advisors:
    - sonnet
  implementers:
    - grok
  reviewers:
    - sol-review
  advisor_policy: before_implementation
  max_review_rounds: 2
  max_total_agent_calls: 20
  max_policy_violation_retries: 2

repository:
  require_git: false
  require_clean_start: false
  exclude_nested_repositories: true
  excluded_paths:
    - private-production/**
    - auth-material/**

output:
  stream_agent_output: true
  retain_raw_output: true
  redact_environment_values: true
```

### Required agent fields

`name`, `adapter`, `model`, `role`, `permissions`, `command`, `prompt_transport`,
`environment_overrides`, `timeout_seconds`, `idle_timeout_seconds`,
`max_repeated_output_lines`, `working_directory`, and optional `fallback_agent`.

`timeout_seconds: 0` disables the wall-clock intervention threshold, which is the default. The idle
watchdog asks after two hours without provider output, and the repetition watchdog asks after 200
consecutive identical output lines. A watchdog never terminates an agent without explicit approval:
an interactive terminal prompts directly, while plugin output emits
`STRINGBEAN_INTERMEDIATE: Watchdog: approval required` for the host to show the user. Missing,
declined, or unavailable approval always means continue. An unchanged condition is reported once;
idle monitoring re-arms after genuine provider output. Set any threshold to `0` to disable it.

Roles: `orchestrator`, `advisor`, `implementer`, `reviewer`, `tester`, `researcher`, `generic`  
Permissions: `read_only` or `read_write`
Optional `mode: high|medium|low` for mode-based agent selection.

### Workflow and repository fields

Active workflow fields:

- `orchestrator`: agent name used for planning.
- `advisors`: optional read-only agents used before implementation when `advisor_policy: before_implementation`.
- `implementers`: write-capable agents used for plan tasks and review fixes.
- `reviewers`: read-only agents used for review rounds.
- `advisor_policy`: `before_implementation` or `never`.
- `max_review_rounds`: review/fix loop limit; `0` skips review.
- `max_total_agent_calls`: total subprocess agent call limit.
- `max_policy_violation_retries`: retry limit after read-only policy violations.

Active repository fields:

- `require_git`: optional strict mode; when `true`, reject a workspace that is not inside a Git worktree. Default: `false`.
- `require_clean_start`: when `true`, dirty repositories fail before agents run.
- `exclude_nested_repositories`: treat nested Git/Hg/SVN worktrees as separate trust boundaries and never inspect their contents. Default: `true`.
- `excluded_paths`: additional ordered gitignore-style patterns that agents must not read, list, modify, or transmit.

Stringbean also reads optional project-local patterns from `.stringbeanignore`. Conventional secret
material (`.env*`, private-key files, credential directories, and prior run artifacts) is excluded by
default, while `.env.example`, `.env.sample`, and `.env.template` remain visible. On Linux, the
subprocess policy preload denies actual opens below concrete excluded paths. The prompt contract also
requires every provider to skip an excluded path without retrying or delegating the read. To inspect a
nested repository intentionally, run `sbx` from that repository's own root or explicitly set
`exclude_nested_repositories: false`.

Reserved config fields are accepted for forward compatibility but are not implemented yet. Non-default values emit `UnsupportedConfigWarning` so they are not silently ignored. They are documented here only to explain warnings from existing configs; new configs should leave them unset:

- `workflow.testers`
- `workflow.researcher`
- `workflow.parallel_read_only_agents`
- `workflow.parallel_write_agents`
- `repository.create_checkpoint_commits`

### Generic adapter example

```yaml
agents:
  local-analyzer:
    adapter: generic
    model: local
    role: reviewer
    permissions: read_only
    command:
      - python
      - scripts/reviewer.py
    prompt_transport: stdin
```

## Preset examples

Preset A (Sol + Claude Sonnet + Grok):

- orchestrator: Codex / GPT-5.6 Sol
- advisor: Claude / current Sonnet alias at medium effort
- implementer: Grok / grok-build
- reviewer: Codex / GPT-5.6 Sol

Preset B (Claude architecture, external implementer):

- orchestrator: Claude
- advisor: Codex
- implementer: Grok
- reviewer: Codex

Preset C (Grok Build only):

- orchestrator, advisor, implementer, and reviewer all use the real `grok-build` model
- low, medium, and high reasoning profiles are available for automatic task routing
- useful when Grok is the only installed provider CLI
- Grok runs use newline-delimited streaming events; hidden thought events are filtered, provider launches and five-second status remain visible, and final text is reconstructed before schema validation

Preset D (fine-grained model mix):

- GPT-5.6: high/medium/low reasoning as separate agents
- GPT-5.5: high/medium/low reasoning as separate agents
- Claude: current `opus`, `sonnet`, and `haiku` aliases (high, medium, and low modes respectively)
- Grok: build/review profiles using headless argv prompt transport (`grok ... -p "<prompt>"`)

`--mode auto` enumerates the configured candidate agents/models for each role, infers high/medium/low from the task text, then selects the lowest-cost adequate candidate for that role. Dry runs include `available_models` and `selection_rationale` so you can see what was considered and why an agent was selected. Simple read/list/show tasks route to low reasoning candidates; complex refactors, migrations, security, architecture, and distributed-system tasks route to stronger high reasoning candidates. Explicit agent choices such as `--advisor claude-sonnet` take precedence over mode selection; use `--orchestrator-mode`, `--advisor-mode`, `--implementer-mode`, and `--reviewer-mode` when you want automatic selection constrained by reasoning level instead.

Claude Code's stable aliases are intentional: `opus`, `sonnet`, and `haiku` resolve to models available for the user's provider and account. Stringbean transparently repairs legacy preset values (`claude-opus-4-8`, `claude-sonnet-5`, and `claude-fable-5`) at launch so older configs no longer fail model validation.

Create it with:

```bash
stringbean init --preset D --force
```

## Run directory format

Each run gets `.stringbean/runs/<run-id>/` with:

- `manifest.json`
- `events.jsonl`
- `state.json`
- `config.snapshot.yaml`
- `task.md`
- `plan.json`
- `final-summary.md`
- `calls/` with call folders:
  - `001-orchestrator/`
  - `002-advisor-sonnet/`
  - `003-implementer/...`
  - each with `prompt.md`, `stdout.txt`, `stderr.txt`, `result.json`, `metadata.json`

## Security and git-safety

- Provider CLIs run as subprocesses with no shell interpolation.
- Environment values are redacted from subprocess environment when output capture is enabled.
- Git is optional. In a plain directory, Stringbean uses bounded filesystem snapshots instead of rejecting the task.
- Sensitive patterns and nested repositories are removed from provider context; Linux subprocesses are denied file opens beneath discovered protected paths.
- The `ro` profile is create-only: new files/directories are allowed, while modifications, deletes, renames, moves, or type changes to existing paths are treated as policy violations.
- Read-only roles can be checked with repository diff snapshots; unauthorized writes are treated as policy violations.
- Dirty repositories are warned on startup and can be blocked with `require_clean_start: true`.
- Provider calls have no wall-clock deadline by default. Idle and repeated-output watchdogs request a human decision without stopping the agent; only explicit approval or user cancellation can terminate a long-running call.
- No forced git commits/pushes or resets are performed.

## Troubleshooting

- `stringbean doctor` fails:
  - check `.stringbean/cli-capabilities.json`
  - verify `.stringbean/config.yaml` points to valid executable names
  - run `stringbean init --force` to recreate defaults
- Dry-run is safe; it does not invoke agents.
- If a provider CLI changes its CLI flags, keep that agent `command` overridden in config rather than editing code.
- If `sbx` starts an older install, run `scripts/install-local-shims.sh` or call `./scripts/sbx` from this checkout.
- If a hosted README example fails, verify the target machine has Python 3.10+ and the relevant provider CLI installed and authenticated.

## Add a new adapter

1. Add a new adapter class under `src/agent_relay/adapters/`
2. Implement `build_command`, `detect`, and prompt transport support
3. Add it to `adapters/__init__.py` and `workflow.py` adapter map
4. Add/adjust templates as needed

## Current limitations

- No concurrent read-only execution scheduler yet (writes are always serialized)
- No built-in cloud backend, no DB, no broker
- Resume relies on local state and artifacts; external artifact loss prevents perfect resumption
- No direct dependency on provider-specific streaming APIs (subprocess stdin plus stdout/stderr capture)

## License

This project is licensed under the MIT License. See `LICENSE` for details.
