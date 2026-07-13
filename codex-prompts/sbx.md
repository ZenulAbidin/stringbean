---
description: Run Stringbean via sbx and return the visible final result
argument-hint: TASK [--rw] [--mode auto|low|medium|high]
---

Run Stringbean for this request using:

```bash
sbx $ARGUMENTS --codex-final
```

While the command is running, use only lines beginning with `STRINGBEAN_INTERMEDIATE:` for
brief user-facing status updates. These lines are intermediate status, not final output. Prefer
those concrete lines over generic “still working” messages. Do not expose hidden chain-of-thought,
raw prompts, file dumps, JSON blobs, or full provider logs.

After the command completes, find the final block between `STRINGBEAN_FINAL_START` and
`STRINGBEAN_FINAL_END`. Inside that block, read the text between `STRINGBEAN_RESULT_START` and
`STRINGBEAN_RESULT_END`.

Your final visible response must contain the useful fields from that block, especially `Status`,
`Result`, and `Artifacts`. Do not tell the user to press Ctrl+T. Do not paste raw transcripts,
prompts, JSON, or intermediate tool logs.

If the command fails, return the concise failure reason and any run/artifact path that was printed.
