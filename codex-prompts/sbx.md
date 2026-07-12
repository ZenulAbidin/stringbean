---
description: Run Stringbean via sbx and return the visible final result
argument-hint: TASK [--rw] [--mode auto|low|medium|high]
---

Run Stringbean for this request using:

```bash
sbx $ARGUMENTS --codex-final
```

After the command completes, find the text between `STRINGBEAN_RESULT_START` and
`STRINGBEAN_RESULT_END` in the tool output.

Your final visible response must contain the useful fields from that block, especially `Status`,
`Result`, and `Artifacts`. Do not tell the user to press Ctrl+T. Do not paste raw transcripts,
prompts, JSON, or intermediate tool logs.

If the command fails, return the concise failure reason and any run/artifact path that was printed.
