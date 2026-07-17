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

The Codex plugin uses a local stdio tool process, which intentionally runs outside the model's shell
command sandbox so it can start configured provider CLIs with their normal local authentication and
network access. Only `start_sbx` is pre-approved; it exposes typed Stringbean options rather than raw
commands, executes the plugin's bundled versioned source snapshot, derives its working directory from
Codex-provided sandbox metadata, binds run IDs to the originating Codex thread, strips runtime
overrides, and removes relative or workspace-owned `PATH` entries. Installing and enabling this
plugin is therefore a local trust decision. The skill remains model-visible so Codex can resolve
the established unqualified `$sbx` spelling, but its instructions call `start_sbx` only for an
explicit `$sbx` / `stringbean:sbx` invocation or a direct request to run Stringbean. That
instruction-level gate is not cryptographic proof of the prompt text; enabling the local plugin
grants access to the narrow typed `start_sbx` capability. The provider boundary does not weaken
Stringbean's excluded-path enforcement, and pre-approved runs never download dependencies.

The MCP environment forwards the standard OpenAI, Anthropic, and xAI/Grok API-key variables so
env-authenticated provider CLIs continue to work. As with a direct `sbx` process, configured agent
subprocesses inherit that environment; prefer provider CLI login stores or narrowly scoped keys when
mixing providers. Stringbean redacts environment values from streamed and retained agent output by
default. The current trusted interpreter path is `/usr/bin/python3`, so this Codex integration is
currently supported on Linux/FHS hosts where that interpreter is a final, dependency-complete
Python 3.10 or newer.

Built-in credential exclusions are mandatory. Project `.stringbeanignore` entries and configured
exclusions may use ordered `!` exceptions within their own rule group, but they cannot re-include a
path protected by built-in rules such as `.env`, private keys, credentials, or secret directories.
