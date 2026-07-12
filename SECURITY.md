# Security Policy

stringbean coordinates local subprocesses. We do not collect telemetry or route data through a central service.

## Reporting a security issue

If you think you've found a security issue (for example, unsafe command handling or unsafe local persistence behavior), please report it privately before filing a public issue.

Include:

- command / code path used
- reproduction steps
- affected version / commit
- any potentially exposed files or secrets

## Security expectations

- Do not hardcode credentials in config files.
- Use `permissions` in agent configs carefully for write-capable roles.
- Review `.stringbean/config.yaml` and `.stringbean/runs/*` before sharing publicly.

## Scope

stringbean relies on external CLI tools (codex/claude/grok). Review those tools' own security and authentication behavior as well.
