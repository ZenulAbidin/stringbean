---
name: sbx
description: Run Stringbean's sbx orchestrator from inside Claude Code; delegate local repository tasks to Stringbean; or run /sbx with compact live multi-agent orchestration output.
argument-hint: <task> [--ro|--rw] [--mode auto|low|medium|high] [stringbean flags]
allowed-tools: [Bash, Read, Glob, Grep, TaskOutput]
---

# Stringbean sbx

Use this command to invoke Stringbean from inside Claude Code.

## Arguments

The user invoked this with: $ARGUMENTS

## Procedure

1. Separate `$ARGUMENTS` into task text and Stringbean flags. Pass the task as one quoted shell
   argument and pass every flag as its own shell argument. Never quote task text and flags together.
2. Preserve user-specified Stringbean flags such as `--rw`, `--ro`, `--mode auto`,
   `--mode low`, `--mode medium`, and `--mode high`.
3. Run the plugin wrapper when this repository checkout is available:

```bash
plugins/claude-stringbean/scripts/sbx-claude "<task text>" <flags>
```

If the current working directory is not the Stringbean source checkout, run the installed command:

```bash
sbx "<task text>" <flags> --plugin-compact-output
```

4. Show useful live Stringbean output while the command runs. Lines beginning with
   `STRINGBEAN_INTERMEDIATE:` are live progress only, not final output.
   Run in the foreground when Bash can preserve a live process beyond one tool wait. If the host's
   timeout would terminate the process, start it as a background task and immediately use blocking
   `TaskOutput` calls instead. A timeout is a polling boundary, never permission to kill Stringbean.
   Repeat bounded waits for as many hours as needed while fresh five-second heartbeat or agent-output
   lines continue to arrive. Stop only after a confirmed process failure or an explicit user-approved
   interruption. Do not use `Monitor` or tell the user to wait.
   Each provider subprocess launch is marked with `STRINGBEAN_INTERMEDIATE: Command:`.
   Claude subprocess events are reconstructed live as concise `assistant:`, `Tool Call:`, and
   `Executed:` lines; do not wait for the subprocess to exit before showing those lines.
5. If Stringbean emits `STRINGBEAN_INTERMEDIATE: Watchdog: approval required`, leave the background
   task alive and ask the user whether to stop it. The watchdog line is not authorization to call a
   task-kill operation. Kill that exact task only after an unambiguous yes; otherwise resume
   `TaskOutput` polling. If Claude cannot preserve the task while asking, default to continuing it.
6. Do not end the turn before the command completes or fails, except to request this explicit
   watchdog decision while the background task remains alive.
7. After completion, read the final result between:

```text
STRINGBEAN_FINAL_START
STRINGBEAN_FINAL_END
```

Inside it, read the compatibility result block between:

```text
STRINGBEAN_RESULT_START
STRINGBEAN_RESULT_END
```

8. The visible final answer should report the useful fields from that result block, especially
   `Status`, `Result`, `Error`, `Tasks`, `Review rounds`, and `Artifacts`.

## Output rules

- Keep the compact plugin mode unless the user explicitly requests raw `--plugin-full-output` diagnostics.
- Do not return a provisional "still running" final answer. A final answer requires
  `STRINGBEAN_FINAL_END` or a confirmed process failure.
- Do not paste raw provider logs, prompts, JSON transcripts, or hidden reasoning.
- Prefer the final result block over raw logs. Do not tell the user to inspect a hidden transcript for the answer.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
- Keep the final answer focused on Stringbean's final result.
