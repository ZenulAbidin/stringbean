Build a minimum viable, production-usable multi-agent coding orchestrator.

Working project name: "stringbean"

Goal

Create a small local CLI application that coordinates existing authenticated coding-agent CLIs such as:

- OpenAI Codex CLI
- Anthropic Claude Code
- xAI Grok CLI
- Any future CLI configured by the user

The application must not replace their native interfaces with a large custom UI. It should act as a lightweight process supervisor, workflow engine, and shared-state layer.

The primary use case is:

GPT-5.6 Sol: primary orchestrator
Claude Fable 5: architecture advisor
Grok 4.5: implementation worker
Codex/Sol: final reviewer

However, no provider or model may be hardcoded into the architecture.

Core principles

1. Use existing CLI authentication and subscriptions.
2. Do not require API keys for the MVP.
3. Treat every agent as an external executable.
4. Keep the orchestration layer provider-agnostic.
5. Keep the interface familiar: a normal terminal command with readable streaming output.
6. Preserve complete prompts, responses, logs, and workflow state.
7. Never allow two write-capable agents to modify the same checkout simultaneously.
8. Make interrupted runs resumable.
9. Prefer explicit, inspectable files over hidden framework magic.
10. Build the smallest reliable system that is genuinely usable.

Technology

Use:

- Python 3.12+
- "typer" for the CLI
- "rich" for terminal output
- "pydantic" for configuration and structured result validation
- "PyYAML" for configuration
- "asyncio" for subprocess management
- "pytest" for tests

Use a conventional "src/" package layout and "pyproject.toml".

Do not build:

- A web interface
- A full-screen TUI
- A database server
- A message broker
- A cloud service
- A dependency on OpenRouter
- A dependency on LangChain, CrewAI, AutoGen, or another orchestration framework

The local filesystem is sufficient for MVP state.

User experience

A typical workflow should look like:

stringbean init
stringbean doctor
stringbean run "Add rate limiting to the public API"

The terminal should display a concise stream such as:

Run: 20260711-143122-rate-limiting

[orchestrator: sol] Creating plan...
[advisor: fable] Reviewing architecture...
[orchestrator: sol] Revising plan...
[implementer: grok] Implementing task 1/3...
[implementer: grok] Implementing task 2/3...
[reviewer: sol] Reviewing changes...
[implementer: grok] Applying requested fixes...
[reviewer: sol] Approved.

Run completed.
Tests: 48 passed
Files changed: 7
Log: .stringbean/runs/20260711-143122-rate-limiting/

The user must also be able to run:

stringbean status
stringbean status <run-id>
stringbean resume <run-id>
stringbean logs <run-id>
stringbean agents
stringbean doctor

Configuration

Create a project-local configuration file:

.stringbean/config.yaml

Example:

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
    timeout_seconds: 1800

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
  parallel_read_only_agents: true
  parallel_write_agents: false

repository:
  require_git: true
  require_clean_start: false
  create_checkpoint_commits: false

output:
  stream_agent_output: true
  retain_raw_output: true
  redact_environment_values: true

Also support fully generic command-based agents:

agents:
  local-reviewer:
    adapter: generic
    role: reviewer
    permissions: read_only
    command:
      - custom-agent
      - run
      - --model
      - custom-model
    prompt_transport: stdin

Never use "shell=True".

CLI detection

"stringbean init" and "stringbean doctor" must:

- Detect executables using "shutil.which"
- Check Codex, Claude, and Grok independently
- Run harmless version/help commands
- Report whether each CLI appears installed and usable
- Never assume exact flags without verification
- Store detected command capabilities when useful
- Continue functioning when only one or two CLIs are installed

Built-in adapters may have sensible defaults, but the command and arguments must be overridable in YAML.

If a provider changes its CLI flags, the user should be able to fix the command template without changing Python code.

Architecture

Implement these components.

1. Agent specification

Represent each agent with fields including:

- Name
- Adapter
- Model
- Role
- Permissions
- Command
- Prompt transport
- Environment overrides
- Timeout
- Working-directory behavior
- Optional fallback agent

Supported roles:

- "orchestrator"
- "advisor"
- "implementer"
- "reviewer"
- "tester"
- "researcher"
- "generic"

Supported permissions:

- "read_only"
- "read_write"

2. Adapter interface

Create a small adapter protocol or abstract base class.

An adapter must be able to:

- Validate availability
- Build the subprocess command
- Prepare the prompt
- Launch the process
- Stream stdout and stderr
- Capture the complete output
- Return a normalized result

Implement:

- "CodexAdapter"
- "ClaudeAdapter"
- "GrokAdapter"
- "GenericCLIAdapter"

Keep provider-specific logic isolated inside adapters.

Do not spread Codex-, Claude-, or Grok-specific conditionals throughout the workflow engine.

3. Process runner

Implement a robust asynchronous subprocess runner.

Requirements:

- No "shell=True"
- Stream output while retaining a complete copy
- Record start time, end time, duration, exit code, and command
- Support stdin prompt delivery
- Support configurable timeouts
- Handle Ctrl-C
- Terminate the entire child process group when cancelled
- Save stdout and stderr separately
- Avoid printing secret environment-variable values
- Return useful errors when an executable is missing
- Never silently treat a failed process as success

4. Run directory

Every invocation receives a stable run ID and directory:

.stringbean/runs/<run-id>/

Store:

manifest.json
events.jsonl
state.json
config.snapshot.yaml
task.md
plan.json
final-summary.md

calls/
  001-orchestrator/
    prompt.md
    stdout.txt
    stderr.txt
    result.json
    metadata.json

  002-advisor-fable/
    prompt.md
    stdout.txt
    stderr.txt
    result.json
    metadata.json

The event log should be append-only JSON Lines.

The state file should be written atomically.

A run must be resumable after process termination.

5. Structured agent contracts

Agents should return structured JSON whenever possible, but the system must retain raw text and fail gracefully when parsing fails.

Use clear contracts.

Orchestrator plan

{
  "summary": "Brief description",
  "assumptions": [],
  "tasks": [
    {
      "id": "task-1",
      "title": "Implement middleware",
      "description": "Detailed, context-complete instructions",
      "dependencies": [],
      "recommended_role": "implementer",
      "permissions": "read_write",
      "verification": [
        "Run unit tests",
        "Confirm 429 behavior"
      ]
    }
  ],
  "risks": [],
  "advisor_questions": []
}

Advisor response

{
  "verdict": "approve",
  "severity": "none",
  "summary": "Plan is sound",
  "blockers": [],
  "concerns": [],
  "recommendations": []
}

Allowed advisor verdicts:

- "approve"
- "revise"
- "block"

Implementer response

{
  "status": "completed",
  "summary": "Implemented rate limiting",
  "files_changed": [],
  "commands_run": [],
  "tests": [],
  "remaining_issues": [],
  "handoff_notes": []
}

Reviewer response

{
  "verdict": "approve",
  "summary": "Changes satisfy the task",
  "blocking_issues": [],
  "non_blocking_issues": [],
  "required_fixes": [],
  "tests_recommended": []
}

Allowed review verdicts:

- "approve"
- "changes_requested"
- "reject"

Tell agents to place the final machine-readable object in a clearly delimited JSON block.

Implement a parser that:

1. Searches for a designated JSON block.
2. Falls back to the last valid JSON object in the output.
3. Validates it with Pydantic.
4. Preserves raw output when parsing fails.
5. Records a structured parse error rather than losing the run.

MVP workflow state machine

Implement this workflow:

RECEIVED
   ↓
PLANNING
   ↓
ADVISOR_REVIEW
   ↓
PLAN_REVISION, only when required
   ↓
IMPLEMENTING
   ↓
REVIEWING
   ↓
FIXING, when changes are requested
   ↓
REVIEWING
   ↓
FINALIZING
   ↓
COMPLETED

Also support:

FAILED
CANCELLED
PAUSED

Detailed behavior:

1. The orchestrator receives the user task and repository context.
2. It produces a structured plan.
3. Advisors review the plan.
4. If an advisor blocks or requests revision, send all advice back to the orchestrator.
5. The orchestrator produces a revised plan.
6. Dispatch implementation tasks in dependency order.
7. Use only one write-capable agent at a time.
8. Read-only tasks may run concurrently when enabled.
9. Reviewers inspect the resulting repository changes.
10. When changes are requested, send the issues to an implementer.
11. Repeat review only up to "max_review_rounds".
12. The orchestrator writes the final user-facing summary.
13. Persist state after every transition.

Do not attempt autonomous infinite loops.

Repository context

Before calling agents, collect compact repository context:

- Current working directory
- Git root
- Current branch
- Git status
- Diff summary
- Top-level file listing
- Relevant instruction files when present:
  - "AGENTS.md"
  - "CLAUDE.md"
  - "README.md"
  - ".codex/"
  - ".claude/"
- The original user task
- Results from prior stages

Do not automatically dump the entire repository into every prompt.

Prompts should be context-complete but economical.

Each implementation task must include:

- Overall objective
- Exact task
- Relevant plan section
- Constraints
- Prior advisor recommendations
- Verification requirements
- Files or areas likely involved
- Required structured output contract

Read-only enforcement

For MVP, enforce read-only behavior through multiple layers where practical:

- Provider CLI permission or planning flags when supported
- Explicit system instructions
- A clean environment
- Git diff checks before and after the call

For every read-only agent:

1. Capture the repository diff before execution.
2. Execute the agent.
3. Capture the diff afterward.
4. If the repository changed, report a policy violation.
5. Do not silently retain unauthorized modifications.
6. Offer a safe rollback only for changes attributable to that call.

Do not make destructive rollback decisions when unrelated working-tree changes existed beforehand.

Git safety

- Never run "git reset --hard".
- Never delete untracked user files.
- Never force checkout over user changes.
- Record the initial Git status.
- Record diffs before and after write-capable calls.
- Warn when the repository was dirty before the run.
- Do not automatically commit unless explicitly configured.
- Do not automatically push.
- Do not open pull requests in the MVP.

Optional checkpoint commits may be implemented only when enabled and only after verifying the user’s repository is suitable.

Prompt templates

Store editable prompt templates as package resources or normal text files.

Required templates:

- Orchestrator planning
- Advisor review
- Orchestrator plan revision
- Implementer task
- Reviewer review
- Implementer fix request
- Final orchestration summary

Do not bury all prompts inside Python strings.

Users should be able to inspect and edit the templates.

Practical commands

Implement:

"stringbean init"

- Create ".stringbean/config.yaml"
- Create ".stringbean/templates/" only when the user chooses local overrides
- Detect available CLIs
- Print the generated agent configuration
- Avoid overwriting existing configuration without confirmation

"stringbean doctor"

Check:

- Python version
- Git availability
- Repository state
- Config validity
- Agent executable availability
- CLI versions
- Template availability
- Writable state directory
- Invalid or conflicting permission settings
- Whether a configured write workflow has at least one write-capable agent

Exit nonzero when required functionality is unavailable.

"stringbean run TASK"

Options:

--config PATH
--orchestrator AGENT
--advisor AGENT
--implementer AGENT
--reviewer AGENT
--max-review-rounds N
--no-advisor
--dry-run
--quiet

"--dry-run" must show:

- Selected agents
- Planned stage sequence
- Commands that would be executed
- Permission levels
- State directory

It must not launch agents.

"stringbean resume RUN_ID"

- Load the config snapshot and state
- Identify the last completed stage
- Continue from the first incomplete stage
- Never repeat a completed write stage automatically unless required
- Clearly tell the user what is being resumed

"stringbean status [RUN_ID]"

Show stage, selected agents, completed calls, errors, review round, and run location.

"stringbean logs RUN_ID"

Print or open a readable summary of events and agent calls.

"stringbean agents"

Show configured agents, executable status, role, model, and permissions.

Example configuration presets

During initialization, offer presets without forcing them:

Preset A: Sol with Fable advisor

orchestrator: Codex / GPT-5.6 Sol
advisor: Claude / Fable 5
implementer: Codex / GPT-5.6 Sol
reviewer: Claude / Fable 5

Preset B: Fable architect with external implementers

orchestrator: Claude / Fable 5
advisor: Codex / GPT-5.6 Sol
implementer: Grok / Grok 4.5
reviewer: Codex / GPT-5.6 Sol

Preset C: Single-provider fallback

Use one available CLI for every role while retaining the same role separation and fresh contexts.

Testing

Create comprehensive tests using fake agent executables.

Do not require real Codex, Claude, or Grok access in CI.

Tests must cover:

- Configuration loading and validation
- Generic command construction
- Missing executable handling
- Successful subprocess execution
- Failed subprocess execution
- Timeout behavior
- Structured JSON extraction
- Malformed response handling
- State transitions
- Atomic state persistence
- Resume after interruption
- Advisor approval
- Advisor revision
- Reviewer approval
- Reviewer changes requested
- Maximum review-round enforcement
- Read-only agent repository mutation detection
- Dirty repository handling
- Dry-run behavior
- Redaction of environment values
- Prevention of simultaneous write agents

Provide fake CLI scripts that simulate streaming output and structured responses.

Documentation

Write a useful "README.md" containing:

- What the project does
- What it deliberately does not do
- Architecture diagram
- Installation
- Five-minute quick start
- CLI reference
- Configuration reference
- Built-in adapters
- Generic adapter example
- Sol/Fable/Grok example
- Run-directory format
- Security and Git-safety behavior
- Troubleshooting
- How to add another CLI adapter
- Current MVP limitations

Include this architecture diagram:

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

Acceptance criteria

The project is complete only when all of the following work:

1. "pip install -e ."
2. "stringbean --help"
3. "stringbean init"
4. "stringbean doctor"
5. A full fake-agent workflow can run from planning through review.
6. A failed run can be resumed.
7. Every agent call has persisted prompts, raw output, metadata, and parsed output.
8. The application prevents concurrent write agents.
9. Tests run without real provider credentials.
10. "pytest" passes.
11. The README allows a new user to configure Codex, Claude, and Grok.
12. The implementation is understandable enough for one developer to maintain.

Implementation sequence

Work in this order:

1. Create the package structure and configuration models.
2. Implement run storage and state transitions.
3. Implement the generic subprocess runner.
4. Implement the generic adapter.
5. Implement thin Codex, Claude, and Grok adapter presets.
6. Implement structured response parsing.
7. Implement the workflow engine.
8. Implement CLI commands.
9. Implement fake agents and tests.
10. Write documentation.
11. Run the complete test suite.
12. Perform a final code review and simplify unnecessary abstractions.

Do not stop after writing a design document.

Implement the working project, run its tests, fix failures, and leave the repository in a usable state.

When provider CLI behavior is uncertain, inspect the locally installed CLI using safe "--help" or version commands. Keep uncertain command details configurable rather than inventing unsupported flags.

At completion, report:

- What was implemented
- File structure
- Commands tested
- Test results
- Known limitations
- The exact first commands I should run
