---
description: Run Stringbean via sbx and return the visible final result
argument-hint: TASK [--ro|--rw] [--mode auto|low|medium|high]
---

Run Stringbean for this request using:

```bash
sbx $ARGUMENTS --codex-final
```

## Provider and sensitive-path boundary

- Treat an explicit `sbx` request as authorization to invoke Stringbean's configured hosted
  providers. Their ordinary remote processing of the task text and non-excluded, in-scope
  repository context is inherent to the requested run; do not ask for separate confirmation
  merely because provider execution is non-local. `--ro` and `--rw` govern repository mutation,
  not whether configured providers may run.
- Never read, list, search, summarize, or transmit paths protected by Stringbean's exclusions.
  For a read-only exploration, audit, random walk, or dream, skip sensitive excluded paths and
  continue without asking the user for access. Never weaken or bypass the exclusions.
- When the host sandbox makes provider runtime state such as `$CODEX_HOME` read-only, launch the
  same `sbx` command through the host's standard escalation mechanism from the outset. If a normal
  launch instead fails before an agent starts with a read-only filesystem error or
  `failed to initialize in-process app-server client`, immediately retry that same command through
  the mechanism, preserving the requested Stringbean profile and task. The warning
  `could not create PATH aliases` by itself is nonfatal; keep the run alive unless provider startup
  actually fails. If the host mechanically requires approval, trigger its tool dialog directly and
  describe it as provider runtime-state and subprocess startup access, not as a new privacy or
  data-sharing decision. Do not end with a provisional provider-sharing question in place of the
  run. Do not add `--ignore-sandbox-warnings`, change `--ro`/`--rw`, or weaken excluded-path
  safeguards.

While the command is running, use only lines beginning with `STRINGBEAN_INTERMEDIATE:` for
brief user-facing status updates. These lines are intermediate status or sanitized agent output,
not final output. Prefer those concrete lines over generic “still working” messages. Do not expose
hidden chain-of-thought, raw prompts, file dumps, JSON blobs, or full provider logs.

After the command completes, find the final block between `STRINGBEAN_FINAL_START` and
`STRINGBEAN_FINAL_END`. Inside that block, read the text between `STRINGBEAN_RESULT_START` and
`STRINGBEAN_RESULT_END`.

Your final visible response must contain the useful fields from that block, especially `Status`,
`Result`, and `Artifacts`. Do not tell the user to press Ctrl+T. Do not paste raw transcripts,
prompts, JSON, or intermediate tool logs.

If the command fails, return the concise failure reason and any run/artifact path that was printed.
