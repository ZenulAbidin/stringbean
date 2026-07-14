---
name: sbx
description: Use Stringbean's sbx orchestrator from inside Grok Build; delegate local repository tasks to Stringbean; or run /sbx with full visible multi-agent orchestration output.
when-to-use: Run Stringbean; invoke sbx; delegate this task through Stringbean; use /sbx.
allowed-tools:
  - run_terminal_command
user-invocable: true
disable-model-invocation: true
metadata:
  author: Stringbean
  short-description: Run Stringbean sbx orchestration
---

# Stringbean sbx

Use this skill to invoke Stringbean from inside Grok Build.

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

1. Convert the user's request into the exact task text for Stringbean.
2. Preserve user-specified Stringbean flags such as `--rw`, `--ro`, `--mode auto`,
   `--mode low`, `--mode medium`, and `--mode high`.
3. Run the plugin wrapper:

```bash
plugins/grok-stringbean/scripts/sbx-grok "<task and flags>"
```

If the current working directory is not the Stringbean source checkout, run the installed command:

```bash
sbx "<task and flags>" --plugin-full-output
```

4. Show useful live Stringbean output while the command runs. Lines beginning with
   `STRINGBEAN_INTERMEDIATE:` are live progress only, not final output.
   Use the longest supported wait or a persistent command session. Treat a host timeout as a
   polling boundary, never permission to kill Stringbean. If a running command/session is returned,
   poll every 5-10 seconds for as many hours as needed. Do not kill or replace the command while
   fresh five-second heartbeat or agent-output lines continue to arrive. Stop only after a confirmed
   process failure or an explicit user-approved interruption.
5. If Stringbean emits `STRINGBEAN_INTERMEDIATE: Watchdog: approval required`, preserve the running
   command session and ask the user whether to stop it. The warning itself is not authorization.
   Terminate that exact session only after an unambiguous yes; otherwise keep polling. If Grok cannot
   preserve the session while asking, default to continuing it.
6. After completion, read the final result between:

```text
STRINGBEAN_FINAL_START
STRINGBEAN_FINAL_END
```

Inside it, read the compatibility result block between:

```text
STRINGBEAN_RESULT_START
STRINGBEAN_RESULT_END
```

7. The visible final answer should report the useful fields from that result block, especially
   `Status`, `Result`, `Error`, `Tasks`, `Review rounds`, and `Artifacts`.

Grok provider calls use `streaming-json` internally. Stringbean filters hidden thoughts,
prints an explicit command line for each provider subprocess plus five-second status updates,
and reconstructs the final model text for schema validation.

## Output rules

- Do not paste raw provider logs, prompts, JSON transcripts, or hidden reasoning.
- Prefer the final result block over raw logs. Do not tell the user to inspect a hidden transcript for the answer.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
- Keep the final answer focused on Stringbean's final result.
