---
description: Run Stringbean via sbx and return the visible final result
argument-hint: TASK [--ro|--rw] [--mode auto|low|medium|high]
---

Use the installed Stringbean plugin's `start_sbx` and `poll_sbx` tools for `$ARGUMENTS`.

## Provider and sensitive-path boundary

- Treat this explicit `/prompts:sbx` invocation as authorization to invoke Stringbean's configured
  hosted providers. Their ordinary remote processing of the task text and non-excluded, in-scope
  repository context is inherent to the requested run; do not ask for separate confirmation merely
  because provider execution is non-local. `--ro` and `--rw` govern repository mutation, not
  whether configured providers may run.
- Never read, list, search, summarize, or transmit paths protected by Stringbean's exclusions.
  For a read-only exploration, audit, random walk, or dream, skip sensitive excluded paths and
  continue without asking the user for access. Never weaken or bypass the exclusions.
- `start_sbx` is the narrowly pre-approved provider-launch boundary for this explicit invocation. It
  uses the plugin's bundled versioned Stringbean snapshot and binds the run to Codex's current sandbox workspace.
- Do not invoke `sbx` through a shell, request host escalation, or ask for a second provider-transfer
  approval. If the Stringbean tools are absent, report that installation is incomplete and name
  `scripts/install-codex-prompts.sh` as the repair command. Never weaken excluded-path safeguards.

Separate the task text from flags. Call `start_sbx` exactly once, mapping supported flags to its
typed arguments (`execution_profile`, `mode`, `dry_run`, `no_advisor`, `max_review_rounds`, and
`policy_retries`). Then call `poll_sbx` with the latest `run_id` and `cursor` every five seconds until
all output is drained and status is `completed`, `failed`, or `cancelled`. A finite tool timeout is
only a polling boundary; continue the same run until it finishes.

While the run is active, use only lines beginning with `STRINGBEAN_INTERMEDIATE:` for
brief user-facing status updates. These lines are intermediate status or sanitized agent output,
not final output. Prefer those concrete lines over generic “still working” messages. Do not expose
hidden chain-of-thought, raw prompts, file dumps, JSON blobs, or full provider logs.

If Stringbean prints `STRINGBEAN_INTERMEDIATE: Watchdog: approval required`, keep the run alive and
ask whether to stop it. Call `cancel_sbx` with `confirmed_by_user: true` only after an explicit yes;
otherwise continue polling.

After the run completes, find the final block between `STRINGBEAN_FINAL_START` and
`STRINGBEAN_FINAL_END`. Inside that block, read the text between `STRINGBEAN_RESULT_START` and
`STRINGBEAN_RESULT_END`.

Your final visible response must contain the useful fields from that block, especially `Status`,
`Result`, and `Artifacts`. Do not tell the user to press Ctrl+T. Do not paste raw transcripts,
prompts, JSON, or intermediate tool logs.

If the run fails, return the concise failure reason and any run/artifact path that was printed.
