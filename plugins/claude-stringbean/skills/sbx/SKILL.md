---
name: sbx
description: Run Stringbean's sbx orchestrator from inside Claude Code; delegate local repository tasks to Stringbean; or run /sbx with compact live multi-agent orchestration output.
argument-hint: <task> [--ro|--rw] [--mode auto|low|medium|high] [stringbean flags]
allowed-tools: [Bash, Read, Glob, Grep, Monitor, TaskOutput, TaskStop]
user-invocable: true
disable-model-invocation: true
---

# Stringbean sbx

Use this command to invoke Stringbean from inside Claude Code.

## Arguments

The user invoked this with: $ARGUMENTS

## Provider and sensitive-path boundary

- Treat an explicit `sbx` request as authorization to invoke Stringbean's configured hosted
  providers. Their ordinary remote processing of the task text and non-excluded, in-scope
  repository context is inherent to the requested run; do not ask for separate confirmation
  merely because provider execution is non-local. `--ro` and `--rw` govern repository mutation,
  not whether configured providers may run.
- Never read, list, search, summarize, or transmit paths protected by Stringbean's exclusions.
  For a read-only exploration, audit, random walk, or dream, skip sensitive excluded paths and
  continue without asking the user for access. Never weaken or bypass the exclusions.

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

4. Run the selected command with `Monitor`, not a foreground `Bash` call. Pass the complete wrapper
   command to `Monitor`, set `persistent: true`, and describe it as the active Stringbean run.
   `Monitor` owns the subprocess and feeds each stdout/stderr line back while it runs, avoiding
   Claude Code's foreground Bash timeout and batched tool-result behavior. A monitor timeout or host
   timeout is a polling boundary, never permission to kill Stringbean.
5. Make meaningful monitor events visible to the user as they arrive. Lines beginning with
   `STRINGBEAN_INTERMEDIATE:` are live progress only, not final output. Relay new stage changes,
   provider `assistant:`, `Tool Call:`, `Executed:`, and failure lines in concise normal assistant
   updates; suppress duplicate five-second heartbeat lines. Do not leave all intermediate events
   hidden inside the Monitor transcript and do not expose private chain-of-thought.
   Each provider subprocess launch is marked with `STRINGBEAN_INTERMEDIATE: Command:`.
6. If `Monitor` is unavailable in this Claude deployment, use `Bash` with
   `run_in_background: true`. Read the returned task output file incrementally with `Read`; use short,
   non-blocking `TaskOutput` polls only when no output file is provided. Never make one blocking
   `TaskOutput` call that waits for the whole run. Continue bounded polling for as many hours as
   needed while the task is active; stop only after failure or an explicit user-approved
   interruption.
7. If Stringbean emits `STRINGBEAN_INTERMEDIATE: Watchdog: approval required`, leave the monitored
   task alive and ask the user whether to stop it. The watchdog line is not authorization to call a
   task-kill operation. Kill that exact task only after an unambiguous yes; otherwise resume
   monitoring. If Claude cannot preserve the task while asking, default to continuing it.
8. Do not end the turn before the command completes or fails, except to request this explicit
   watchdog decision while the background task remains alive.
9. After completion, read the final result between:

```text
STRINGBEAN_FINAL_START
STRINGBEAN_FINAL_END
```

Inside it, read the compatibility result block between:

```text
STRINGBEAN_RESULT_START
STRINGBEAN_RESULT_END
```

10. The visible final answer should report the useful fields from that result block, especially
   `Status`, `Result`, `Error`, `Tasks`, `Review rounds`, and `Artifacts`.

## Output rules

- Keep the compact plugin mode unless the user explicitly requests raw `--plugin-full-output` diagnostics.
- Do not return a provisional "still running" final answer. A final answer requires
  `STRINGBEAN_FINAL_END` or a confirmed process failure.
- Do not paste raw provider logs, prompts, JSON transcripts, or hidden reasoning.
- Prefer the final result block over raw logs. Do not tell the user to inspect a hidden transcript for the answer.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
- Keep the final answer focused on Stringbean's final result.
