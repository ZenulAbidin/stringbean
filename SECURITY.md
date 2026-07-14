# Security Policy

stringbean coordinates local subprocesses. We do not collect telemetry or route data through a central service.

## Reporting a security issue

If you think you've found a security issue (for example, unsafe command handling or unsafe local persistence behavior), please report it privately before filing a public issue.

Include:

- command / code path used
- reproduction steps
- affected version / commit
- any potentially exposed files or secrets

Please report security issues to ali@zenulabidin.com.

## Security expectations

- Do not hardcode credentials in config files.
- Use `permissions` in agent configs carefully for write-capable roles.
- Put project-specific production/auth material in `.stringbeanignore` or `repository.excluded_paths`; nested repositories are separate trust boundaries by default.
- Review `.stringbean/config.yaml` and `.stringbean/runs/*` before sharing publicly.
- Do not commit `.stringbean/cli-capabilities.json`; it is machine-local probe output.
- Do not publish local run artifacts as hosted docs without reviewing prompts, stdout/stderr, metadata, and final summaries for private data.

## Scope

stringbean relies on external CLI tools (codex/claude/grok). Review those tools' own security and authentication behavior as well.

On Linux, Stringbean's policy preload denies provider subprocess opens under concrete excluded paths.
The same exclusions are removed from generated provider context on every platform. This is a local
defense-in-depth boundary, not a replacement for provider account controls or host-level isolation.

Hosting the repository or package does not host executions. Users run Stringbean locally, with their
own provider CLI authentication and their own workspace permissions.
