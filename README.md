# stringbean

stringbean is a lightweight local orchestrator for coding-agent CLIs. It coordinates multiple
agents (Codex, Claude, Grok, or any generic CLI adapter) without replacing native interfaces, keeps a
filesystem audit trail, and runs a small resumable workflow.

It is not a provider UI and does not handle API keys. It is intentionally minimal: a local subprocess
supervisor with inspectable artifacts.

## What this project does

- Runs an MCP-style workflow (planning → review → implementation → review) across configured agent roles
- Supports multiple providers through adapter plugins:
  - Codex (`codex`)
  - Claude (`claude`)
  - Grok (`grok`)
  - Generic CLI adapters (`generic`)
- Persists complete run artifacts: prompts, raw output, parsed output, metadata, state transitions, and event log
- Can be resumed after interruption using persisted state
- Enforces basic repository safety and read-only behavior checks

## What it does not do

- Does not expose a web UI, full-screen TUI, database, broker, or cloud service
- Does not replace provider-specific UIs or authentication flows
- Does not require API keys directly (it delegates auth to local provider CLIs)
- Does not perform autonomous long-running loops beyond the configured workflow

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

- Python 3.10+ (Python 3.12+ recommended)
- Local install of desired provider CLIs (optional)
- Optional git availability for best safety checks

## Install

```bash
pip install -e .
```

## Quick start (5 minutes)

```bash
stringbean init
stringbean doctor
stringbean run "Inspect lightweight input validation in API endpoint"
stringbean run "Implement lightweight input validation in API endpoint"
stringbean status
stringbean logs <run-id>
```

For the lightweight slash-style flow from any directory, use the repo wrapper:

```bash
# from any directory
~/Documents/stringbean/scripts/sbx "Inspect feature X"
~/Documents/stringbean/scripts/sbx "Implement feature X"
# or explicitly:
~/Documents/stringbean/scripts/stringbean sbx "Implement feature X"
# or, after sourcing the hook below:
# source ~/Documents/stringbean/scripts/sbx-zsh-hook.zsh
stringbean sbx "Implement feature X"
```

If `stringbean` points to `~/.local/bin/stringbean`, it may be an old shim and can pick the wrong Python.

Use one of the repo scripts above.  
From any folder, it should also work without `STRINGBEAN_ROOT` if you use:

```bash
./scripts/install-local-shims.sh
```

If your environment cannot write to `~/.local/bin`, add repo scripts to PATH once:

```bash
export PATH="$HOME/Documents/stringbean/scripts:$PATH"
```

Use `--dry-run` to inspect what would happen before running:

```bash
stringbean run --dry-run "Implement auth checks" 
```

## CLI reference

- `stringbean init`
  - Creates `.stringbean/config.yaml` in the current project, or `~/.stringbean/config.yaml` if no local project config directory is discovered
  - Optional `--preset A|B|C` (default A)
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
    - `rw` is the default profile: write-capable agents may modify files in service of the task. Read-only roles are still diff-checked.
    - `ro` is the create-only profile: subagents can inspect, run commands, and create new files/directories, but modifications, deletes, renames, moves, or type changes to pre-existing repository paths are treated as policy violations and rolled back where safe.
    - Codex agents are launched with explicit approval/sandbox flags instead of inherited defaults: `ro` uses workspace-write with Stringbean diff enforcement; `rw` uses danger-full-access at the provider layer while Stringbean still diff-checks read-only roles.
    - Subagents receive a Stringbean denylist for destructive commands such as `rm`, `sudo`, `dd`, `mkfs`, `shutdown`, and destructive git operations such as `git reset`, `git clean`, and `git push`.
  - `--policy-retries N`
    - Retries an agent call after filesystem policy violations by reframing the role prompt as analysis-only and naming the forbidden paths. Default: `workflow.max_policy_violation_retries` (`2`).
  - `--max-review-rounds N`
  - `--no-advisor`
  - `--dry-run`
  - `--no-agent-stream` / `--no-agent-output` hides the live provider stdout/stderr stream. By default Stringbean shows selective formatted provider output: prompt echoes and CLI boilerplate are suppressed, visible escapes such as `\n` are decoded, structured JSON answers are collapsed into readable result lines, tool output bodies are capped at three visible lines, terminal output uses TTY-aware bold/white labels, and raw stdout/stderr are still retained in run artifacts.
  - `--codex-final` emits only a compact `STRINGBEAN_RESULT_START` / `STRINGBEAN_RESULT_END` block for Codex custom prompts to mirror into the visible final answer.
  - `--quiet`
  - `--run-id`
- `stringbean resume RUN_ID`
  - Continue a partial run from persisted state
- `stringbean status [RUN_ID]`
  - Show run stage, status, errors, review rounds, run location
- `stringbean logs RUN_ID`
  - Show event log and call artifacts summary

### Installation alias for quick launch

- `scripts/stringbean` is a local shim that always routes into repo code.

```bash
~/Documents/stringbean/scripts/stringbean run "Quick task"
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
source ~/Documents/stringbean/scripts/sbx-zsh-hook.zsh
```

Then type:

```bash
/sbx "Fix login validation bug" --mode high
```

If `sbx` is still being resolved to an old global script, this hook also defines a `sbx` shell function so `sbx ...` always uses the repo-local wrapper.

### Codex plugin wrapper

For use inside Codex, prefer the local Stringbean plugin. It installs a `stringbean:sbx` skill that tells Codex to run `sbx --codex-final`, surface compact progress lines during long runs, and mirror the sentinel-wrapped result into the visible final answer.

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

`--codex-final` keeps raw provider output hidden, but progress is on by default. Use `--no-codex-progress` for a silent run, or `--codex-progress-interval 10` to make long-running heartbeat lines more frequent.

Codex plugins are installed from this repo's local marketplace at `.agents/plugins/marketplace.json`.

### Codex custom prompt wrapper

Codex collapses shell-command output into the transcript panel, so Stringbean includes a custom prompt wrapper that tells Codex to run `sbx --codex-final`, summarize compact progress lines while it runs, and copy the sentinel-wrapped result into the visible final answer.

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
- Before announcing publicly, verify:
  - `python3.10 -m pytest -q` passes
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
    timeout_seconds: 1800

  fable:
    adapter: claude
    model: fable
    role: advisor
    permissions: read_only
    command: null
    timeout_seconds: 900

  grok:
    adapter: grok
    model: grok-4.5
    role: implementer
    permissions: read_write
    command: null

workflow:
  orchestrator: sol
  advisors:
    - fable
  implementers:
    - grok
  reviewers:
    - sol
  advisor_policy: before_implementation
  max_review_rounds: 2
  max_total_agent_calls: 20
  max_policy_violation_retries: 2

repository:
  require_git: true
  require_clean_start: false

output:
  stream_agent_output: true
  retain_raw_output: true
  redact_environment_values: true
```

### Required agent fields

`name`, `adapter`, `model`, `role`, `permissions`, `command`, `prompt_transport`,
`environment_overrides`, `timeout_seconds`, `working_directory`, and optional
`fallback_agent`.

Roles: `orchestrator`, `advisor`, `implementer`, `reviewer`, `tester`, `researcher`, `generic`  
Permissions: `read_only` or `read_write`
Optional `mode: high|medium|low` for mode-based agent selection.

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

Preset A (Sol + Fable + Grok):

- orchestrator: Codex / GPT-5.6 Sol
- advisor: Claude / Fable 5
- implementer: Grok / Grok 4.5
- reviewer: Codex / GPT-5.6 Sol

Preset B (Fable architecture, external implementer):

- orchestrator: Claude
- advisor: Codex
- implementer: Grok
- reviewer: Codex

Preset C (single-provider fallback):

- same role set reused for local fallback command

Preset D (fine-grained model mix):

- GPT-5.6: high/medium/low reasoning as separate agents
- GPT-5.5: high/medium/low reasoning as separate agents
- Claude: Opus 4.8, Fable 5, Sonnet 5
- Grok: build/review profiles

`--mode auto` will infer high/medium/low from task text and select matching agents across roles where available. You can pin by role with `--orchestrator-mode`, `--advisor-mode`, `--implementer-mode`, and `--reviewer-mode`.

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
  - `002-advisor-fable/`
  - `003-implementer/...`
  - each with `prompt.md`, `stdout.txt`, `stderr.txt`, `result.json`, `metadata.json`

## Security and git-safety

- Provider CLIs run as subprocesses with no shell interpolation.
- Environment values are redacted from subprocess environment when output capture is enabled.
- The `ro` profile is create-only: new files/directories are allowed, while modifications, deletes, renames, moves, or type changes to existing paths are treated as policy violations.
- Read-only roles can be checked with repository diff snapshots; unauthorized writes are treated as policy violations.
- Dirty repositories are warned on startup and can be blocked with `require_clean_start: true`.
- No forced git commits/pushes or resets are performed.

## Troubleshooting

- `stringbean doctor` fails:
  - check `.stringbean/cli-capabilities.json`
  - verify `.stringbean/config.yaml` points to valid executable names
  - run `stringbean init --force` to recreate defaults
- Dry-run is safe; it does not invoke agents.
- If a provider CLI changes its CLI flags, keep that agent `command` overridden in config rather than editing code.

## Add a new adapter

1. Add a new adapter class under `src/agent_relay/adapters/`
2. Implement `build_command`, `detect`, and prompt transport support
3. Add it to `adapters/__init__.py` and `workflow.py` adapter map
4. Add/adjust templates as needed

## Current MVP limitations

- No concurrent read-only execution scheduler yet (writes are always serialized)
- No built-in cloud backend, no DB, no broker
- Resume relies on local state and artifacts; external artifact loss prevents perfect resumption
- No direct dependency on provider-specific streaming APIs (subprocess stdin plus stdout/stderr capture)
