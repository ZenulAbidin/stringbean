---
name: "sbx"
description: "Use when the user wants to run Stringbean, sbx, or local agent orchestration from inside Codex; delegate a task to Stringbean's orchestrator; or get a compact final result from sbx without raw transcripts."
---

# Stringbean sbx

Use this skill to invoke Stringbean from inside Codex.

## Behavior

1. Convert the user's request into the exact task text for Stringbean.
2. Run Stringbean with compact Codex output enabled:

```bash
sbx "<task and flags>" --codex-final
```

If the current working directory is the Stringbean repository and the repo-local wrapper exists,
this equivalent command is also acceptable:

```bash
plugins/stringbean/scripts/sbx-codex "<task and flags>"
```

3. Preserve user-specified Stringbean flags such as `--rw`, `--ro`, `--mode auto`,
   `--mode low`, `--mode medium`, and `--mode high`.
4. Do not add a permissions flag unless the user asks for one. Stringbean's default profile is
   `rw`; use `--ro` only when the user explicitly asks for create-only/read-only behavior.
5. Treat lines beginning with `STRINGBEAN_INTERMEDIATE:` as live status or sanitized agent-output
   updates only.
   They are not final output and should be summarized briefly while the command runs.
6. After the command finishes, find the final block between:

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

Stringbean emits explicitly marked compact progress and sanitized agent-output lines before the final block:

```text
STRINGBEAN_INTERMEDIATE: Progress: ...
STRINGBEAN_INTERMEDIATE: Agent: ...
STRINGBEAN_INTERMEDIATE: Agent output: ...
```

Use those lines for brief user-facing updates if the run takes time. These lines are already
sanitized: they describe phases, selected agents, parsed summaries, verdicts, bounded
still-running heartbeats, visible assistant messages, tool calls, and capped tool output. They do
not include hidden chain-of-thought or raw transcripts. Do not confuse them with final output, and
do not invent generic progress text when a specific `STRINGBEAN_INTERMEDIATE:` line is available.

## Output rules

- Do not paste raw provider logs, prompts, JSON, or transcripts.
- Do not tell the user to press Ctrl+T to see the result.
- Do not expose hidden chain-of-thought. Progress lines are observable status, not reasoning.
- Keep the final answer short and focused on Stringbean's final result.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
