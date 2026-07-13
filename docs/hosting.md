# Hosting stringbean

stringbean is ready to host as a source repository, Python package, and downloadable release artifact.
It is not a hosted service: the workflow runs locally on the user's machine and shells out to locally
installed provider CLIs.

## What can be hosted

- Public source repository with the files in this tree.
- Project documentation: `README.md`, `CONTRIBUTING.md`, `SECURITY.md`, `RELEASE.md`, `CHANGELOG.md`, and files under `docs/`.
- Python package artifacts produced by `python -m build`.
- Release notes and optional wheel/sdist attachments on GitHub Releases.
- Optional package publication to PyPI after `python -m twine check dist/*` passes.

## What remains local-only

- Provider authentication and API keys. Codex, Claude, Grok, and generic tools keep their own local auth flows.
- Runtime state under `.stringbean/runs/`.
- Machine probes such as `.stringbean/cli-capabilities.json`.
- User configuration under `.stringbean/config.yaml` or `~/.stringbean/config.yaml`.
- Secret material, private keys, and environment files.

Do not publish generated run artifacts unless you have reviewed their prompts, transcripts, metadata,
and captured stdout/stderr for private project data.

## Minimum hosted release checklist

1. Confirm version consistency:

   ```bash
   python -m agent_relay.cli --version
   ```

2. Run tests and package validation:

   ```bash
   python -m pytest -q
   python -m build
   python -m twine check dist/*
   ```

3. Verify local CLI behavior:

   ```bash
   stringbean --version
   stringbean doctor
   ./scripts/sbx "Quick smoke test" --dry-run --mode low
   ```

4. Review docs and local links before publishing. At minimum, confirm every relative Markdown link
   in `README.md`, `RELEASE.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, and `docs/*.md`
   resolves inside the repository:

   ```bash
   rg -n "\[[^]]+\]\(([^)#][^)]+)\)" README.md RELEASE.md CONTRIBUTING.md SECURITY.md CHANGELOG.md docs/*.md
   ```

5. Publish only the source tree and release artifacts. Leave local runtime directories untracked.

## Repository settings

- Keep issue reporting enabled for CLI, adapter, and documentation bugs.
- Use private vulnerability reporting if the hosting platform supports it.
- Protect release tags such as `v0.2.0` after publication.
- Require CI to run tests and package build checks before merging release branches.

## Public copy

Use `docs/x_post.md` as a draft announcement only after the release URL exists. Replace
`github.com/<your-org>/stringbean` with the final hosted repository or release URL.
