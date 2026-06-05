# Releasing

Developer notes for cutting a release of `tcl_lyon`. End users should follow the
install instructions in [`README.md`](README.md) instead.

## How it works

Releases are **tag-driven**. Pushing a `vX.Y.Z` tag triggers
[`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. Checks the tag matches the `version` in `custom_components/tcl_lyon/manifest.json`
   (the job fails if they differ).
2. Zips the integration into `tcl_lyon.zip` (excluding `__pycache__`/`.pyc`), with
   `manifest.json` at the archive root.
3. Publishes a GitHub Release with auto-generated notes and the zip attached.

HACS is configured with `zip_release` + `filename` in `hacs.json`, so users install
**from `tcl_lyon.zip`** — not from the source tree. A release only appears in HACS once
this workflow has attached that asset.

## Versioning

[Semantic Versioning](https://semver.org/). The source of truth for a release is the
`version` field in `manifest.json`; the tag is `v` + that value (e.g. manifest `0.6.0`
→ tag `v0.6.0`).

Minimum supported platform: **Home Assistant 2025.2** / **Python 3.13** (declared in
`hacs.json` and `pyproject.toml`). Bump those if you raise the floor.

## Steps

1. **Make sure `main` is green.** Lint and tests must pass — CI runs them on every push,
   or run locally:
   ```bash
   ruff check . && ruff format --check .
   pytest
   ```

2. **Pick the version** per SemVer (e.g. `0.6.0`). Below, `X.Y.Z` is that number.

3. **Bump `manifest.json`:**
   ```jsonc
   // custom_components/tcl_lyon/manifest.json
   "version": "X.Y.Z",
   ```

4. **Update `CHANGELOG.md`:** rename the `## [Unreleased]` heading to
   `## [X.Y.Z] - YYYY-MM-DD` and start a fresh empty `## [Unreleased]` section above it.

5. **Commit** the bump on `main`:
   ```bash
   git add custom_components/tcl_lyon/manifest.json CHANGELOG.md
   git commit -m "Release X.Y.Z"
   git push
   ```

6. **Tag and push the tag** (this is what triggers the release):
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

7. **Watch the workflow** under the repo's **Actions → Release** tab. On success it
   creates the GitHub Release with `tcl_lyon.zip` attached. If it fails on the version
   check, the tag and `manifest.json` disagree — see "Fixing a bad tag".

8. **Verify in HACS** (optional): the new version should be offered for update within a
   few hours, or immediately after redownloading the integration in HACS.

## Fixing a bad tag

If the workflow fails (e.g. tag ≠ manifest version) or you tagged the wrong commit,
delete the tag, fix the cause, and re-tag:

```bash
git tag -d vX.Y.Z                 # delete locally
git push origin :refs/tags/vX.Y.Z # delete on the remote
# ...correct manifest.json / commit...
git tag vX.Y.Z && git push origin vX.Y.Z
```

If a GitHub Release was already created, delete it in the GitHub UI before re-tagging.
