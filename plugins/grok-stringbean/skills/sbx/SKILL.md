---
name: sbx
description: Use Stringbean's sbx orchestrator from inside Grok Build; delegate local repository tasks to Stringbean; or run /sbx for compact multi-agent orchestration.
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
sbx "<task and flags>" --plugin-final
```

4. Treat lines beginning with `STRINGBEAN_INTERMEDIATE:` as live progress only.
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
- Do not tell the user to inspect a hidden transcript for the answer.
- If Stringbean fails, report the concise failure reason and any artifact path that was printed.
- Keep the final answer focused on Stringbean's final result.
