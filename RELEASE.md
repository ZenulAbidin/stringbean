# Release checklist (v0.1.0 public release)

This project uses a lightweight release flow you can run from a clean local checkout.

## 1) Pre-release validation

- [ ] Confirm a clean working tree for release contents.
- [ ] Confirm generated local state is not staged:
  - `.stringbean/runs/`
  - `.stringbean/cli-capabilities.json`
  - `.stringbean-runtime/`
  - `implemented.txt`
- [ ] Update metadata:
  - `pyproject.toml` version
  - `src/agent_relay/__init__.py` `__version__`
  - `plugins/stringbean/.codex-plugin/plugin.json` version
  - `plugins/grok-stringbean/.grok-plugin/plugin.json` version
  - `plugins/claude-stringbean/.claude-plugin/plugin.json` version
  - `CHANGELOG.md`
- [ ] Run full tests:

  ```bash
  python -m pytest -q
  ```

- [ ] Verify the CLI still works for core commands:

  ```bash
  stringbean --version
  sbx --help
  ./plugins/grok-stringbean/scripts/sbx-grok "Quick plugin smoke test" --dry-run
  ./plugins/stringbean/scripts/sbx-codex "Quick plugin smoke test" --dry-run
  ./plugins/claude-stringbean/scripts/sbx-claude "Quick plugin smoke test" --dry-run
  sbx "Quick full-output smoke test" --dry-run --plugin-full-output
  stringbean init
  stringbean doctor
  stringbean status
  ```

- [ ] Confirm example configs and docs are coherent (`README.md`, `RELEASE.md`, `CONTRIBUTING.md`).
- [ ] Smoke test run path with a small task (from any directory if desired):

  ```bash
  ./scripts/sbx "Quick smoke test" --dry-run --mode low
  sbx "Quick smoke test through installed entrypoint" --dry-run --mode low
  grok plugin validate plugins/grok-stringbean
  claude plugin validate plugins/claude-stringbean
  ```

## 2) Build & package

```bash
python -m pip install build twine
python -m build
python -m twine check dist/*
```

Artifacts should appear in `dist/`:

- `stringbean-<version>-py3-none-any.whl`
- `stringbean-<version>.tar.gz`

## 3) Create GitHub release

1. Create the public repo and push the prepared tree:

   ```bash
   gh repo create stringbean --public --source=. --remote=origin --push
   ```

2. Tag the release commit:

   ```bash
   git tag -a v0.1.0 -m "Release v0.1.0"
   git push origin v0.1.0
   ```

3. On GitHub: `Releases → Create a new release`
   - Tag: `v0.1.0`
   - Title: `stringbean v0.1.0`
   - Paste changelog excerpt from `CHANGELOG.md`
   - Attach wheel/tarball artifacts from `dist/` (optional if PyPI handles distribution)

## 4) Optional: publish to PyPI

```bash
python -m pip install twine
python -m twine check dist/*
python -m twine upload dist/*
```

## 5) Post-launch announcements

Use the draft in `docs/x_post.md` (or adapt) and include:

- link to release page
- one-liner value summary
- what model/tool profiles are supported in this version

## 6) After release

- Update pinned defaults if needed.
- Open any follow-up issues from smoke test notes.
- Keep a changelog entry for known limitations and next iteration.
