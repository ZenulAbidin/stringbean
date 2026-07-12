# Release checklist (v1.0 release prep)

This project uses a lightweight release flow you can run from a clean local checkout.

## 1) Pre-release validation

- [ ] Confirm a clean working tree for release contents.
- [ ] Update metadata:
  - `pyproject.toml` version
  - `CHANGELOG.md`
- [ ] Run full tests:

  ```bash
  python3.10 -m pytest -q
  ```

- [ ] Verify the CLI still works for core commands:

  ```bash
  PYTHONPATH=src python3.10 -m agent_relay.cli --help  # if using module path directly
  stringbean init
  stringbean doctor
  stringbean status
  ```

- [ ] Confirm example configs and docs are coherent (`README.md`, `RELEASE.md`, `CONTRIBUTING.md`).
- [ ] Smoke test run path with a small task (from any directory if desired):

  ```bash
  ~/Documents/stringbean/scripts/sbx "Quick smoke test" --mode low
  ~/Documents/stringbean/scripts/sbx "Quick write smoke test" --rw --mode low
  ```

## 2) Build & package

```bash
python3.10 -m pip install build
python3.10 -m build
```

Artifacts should appear in `dist/`:

- `stringbean-<version>-py3-none-any.whl`
- `stringbean-<version>.tar.gz`

## 3) Create GitHub release

1. Commit release changes with a clear message (`chore: prep vX.Y.Z`).
2. Tag the commit:

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
python3.10 -m pip install twine
python3.10 -m twine check dist/*
python3.10 -m twine upload dist/*
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
