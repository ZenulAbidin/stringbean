---
name: sbx
description: Run Stringbean's sbx orchestrator from inside Claude Code; delegate local repository tasks to Stringbean; or run /sbx with full visible multi-agent orchestration output.
argument-hint: <task> [--ro|--rw] [--mode auto|low|medium|high] [stringbean flags]
allowed-tools: [Bash, Read, Glob, Grep]
---

# Stringbean sbx

Use this command to invoke Stringbean from inside Claude Code.

## Arguments

The user invoked this with: $ARGUMENTS

## Procedure

1. Convert `$ARGUMENTS` into the exact task text and flags for Stringbean.
2. Preserve user-specified Stringbean flags such as `--rw`, `--ro`, `--mode auto`,
   `--mode low`, `--mode medium`, and `--mode high`.
3. Run the plugin wrapper when this repository checkout is available:

```bash
plugins/claude-stringbean/scripts/sbx-claude "$ARGUMENTS"
```

If the current working directory is not the Stringbean source checkout, run the installed command:

```bash
sbx "$ARGUMENTS" --plugin-full-output
```

4. Show useful live Stringbean output while the command runs. Lines beginning with
   `STRINGBEAN_INTERMEDIATE:` are live progress only, not final output.
   Set the Bash timeout to at least 1,800 seconds when available. If the command is yielded
   as a running process, poll it every 5-10 seconds and do not terminate it while fresh
   five-second heartbeat or agent-output lines continue to arrive.
   Each provider subprocess launch is marked with `STRINGBEAN_INTERMEDIATE: Command:`.
5. After completion, read the final result between:

```text
STRINGBEAN_FINAL_START
STRINGBEAN_FINAL_END
```

Inside it, read the compatibility result block between:

```text
STRINGBEAN_RESULT_START
STRINGBEAN_RESULT_END
```

6. The visible final answer should report the useful fields from that result block, especially
   `Status`, `Result`, `Error`, `Tasks`, `Review rounds`, and `Artifacts`.

## Output rules

- Do not paste raw provider logs, prompts, JSON transcripts, or hidden reasoning.
- Prefer the final result block over raw logs. Do not tell the user to inspect a hidden transcript for the answer.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
- Keep the final answer focused on Stringbean's final result.
