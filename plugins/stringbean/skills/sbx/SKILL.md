---
name: "sbx"
description: "Use when the user wants to run Stringbean, sbx, or local agent orchestration from inside Codex; delegate a task to Stringbean's orchestrator; or get a full visible Stringbean run with a final sentinel result."
---

# Stringbean sbx

Use this skill to invoke Stringbean from inside Codex.

## Provider and sensitive-path boundary

- Treat an explicit `$sbx` or `stringbean:sbx` invocation as authorization to invoke Stringbean's
  configured hosted providers. Their ordinary remote processing of the task text and non-excluded,
  in-scope repository context is inherent to the requested run; do not ask for separate
  confirmation merely because provider execution is non-local. `--ro` and `--rw` govern repository
  mutation, not whether configured providers may run.
- Do not call `start_sbx` for an incidental mention of Stringbean or `sbx`. Proceed only when the
  current user request explicitly invokes `$sbx` / `stringbean:sbx` or directly asks you to run
  Stringbean. This instruction-level gate keeps the established unqualified `$sbx` spelling
  discoverable even though plugin skills are namespaced by Codex.
- Never read, list, search, summarize, or transmit paths protected by Stringbean's exclusions.
  For a read-only exploration, audit, random walk, or dream, skip sensitive excluded paths and
  continue without asking the user for access. Never weaken or bypass the exclusions.

## Trusted Codex plugin tools

- Use only the Stringbean plugin's `start_sbx` and `poll_sbx` tools. `start_sbx` is the narrowly
  pre-approved provider-launch boundary for an explicit invocation of this skill; it runs the
  plugin's bundled versioned Stringbean snapshot and binds the run to Codex's current sandbox workspace.
- Do not invoke `sbx` through a shell, request host escalation, or ask for a second provider-transfer
  approval. The user's explicit invocation already authorizes ordinary processing by configured
  providers, while Stringbean continues to enforce excluded paths.
- If these tools are unavailable, report that the Codex plugin installation is incomplete and name
  `scripts/install-codex-plugin.sh` as the repair command. Do not fall back to a shell launcher.

## Behavior

1. Convert the user's request into the exact `task` text for Stringbean. Keep flags out of that text.
2. Call `start_sbx` exactly once. Map user flags to its typed arguments: `execution_profile`, `mode`,
   `dry_run`, `no_advisor`, `max_review_rounds`, and `policy_retries`. Do not add an execution profile
   unless the user requests one; the tool and Stringbean both default to `rw`.
3. Repeatedly call `poll_sbx` with the returned `run_id` and latest `cursor`, normally waiting five
   seconds per call. Continue until all output is drained and status is `completed`, `failed`, or
   `cancelled`. A finite tool timeout is a polling boundary, never permission to abandon the run.
4. Show useful live Stringbean output while the run is active. Lines beginning with
   `STRINGBEAN_INTERMEDIATE:` are progress/status lines only, not final output.
   Do not terminate while fresh Stringbean heartbeat or agent-output lines continue to arrive.
   Multi-hour implementation calls are valid. Stop only after a confirmed process failure or an
   explicit user-approved interruption.
5. If Stringbean emits `STRINGBEAN_INTERMEDIATE: Watchdog: approval required`, keep polling and ask
   the user whether to stop that run. The warning is not termination authorization. Call
   `cancel_sbx` for that `run_id` with `confirmed_by_user: true` only after an unambiguous yes;
   otherwise continue polling.
6. After the run finishes, find the final block between:

```text
STRINGBEAN_FINAL_START
STRINGBEAN_FINAL_END
```

Inside it, keep backward compatibility by reading the result block between:

```text
STRINGBEAN_RESULT_START
STRINGBEAN_RESULT_END
```

7. The visible final answer must report the useful fields from that block, especially:
   `Status`, `Result`, `Tasks`, `Review rounds`, and `Artifacts`.

## During the run

Stringbean emits normal visible run output and explicitly marked progress lines before the final block:

```text
STRINGBEAN_INTERMEDIATE: Progress: ...
STRINGBEAN_INTERMEDIATE: Agent: ...
STRINGBEAN_INTERMEDIATE: Agent output: ...
```

Plugin wrappers request a heartbeat every five seconds by default. A live heartbeat means
the command is active, even when the provider is still waiting for its next model response.
Each provider subprocess launch also emits an explicit `STRINGBEAN_INTERMEDIATE: Command:` line.

Use those lines for brief user-facing updates if the run takes time. These lines are already
sanitized: they describe phases, selected agents, parsed summaries, verdicts, bounded
still-running heartbeats, visible assistant messages, tool calls, and capped tool output. They do
not include hidden chain-of-thought or raw transcripts. Do not confuse them with final output, and
do not invent generic progress text when a specific `STRINGBEAN_INTERMEDIATE:` line is available.

## Output rules

- Prefer the visible final result block over raw logs.
- Do not tell the user to press Ctrl+T to see the result.
- Do not expose hidden chain-of-thought. Progress lines are observable status, not reasoning.
- Keep the final answer short and focused on Stringbean's final result.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
