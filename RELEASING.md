# Releasing Repo Doctor

A release is a GitHub release with a verified skill ZIP and its checksum
receipt attached. Nothing is uploaded to PyPI; the skill installs smythe from
PyPI at the range in `runtime.txt`.

## Release assets

| Asset | Contents |
|---|---|
| `repo-doctor-skill-X.Y.Z.zip` | One `repo-doctor/` folder: `SKILL.md`, `README.md`, `runtime.txt`, `LICENSE`, `references/`, `scripts/`, `evals/` and `MANIFEST.json` |
| `repo-doctor-skill-X.Y.Z.json` | Receipt: archive bytes and SHA-256, source commit, skill version and runtime requirement |

`MANIFEST.json` records the skill version, the source commit, the runtime
requirement and the bytes and SHA-256 of every file. The archive inventory is
the `FILES` list in `tools/skill_archive.py`; a test fails when a tracked skill
file is missing from it or a listed file is not tracked.

## Runtime pin

`runtime.txt` holds one exact pin (`smythe==X.Y.Z`) or one bounded range
(`smythe>=X.Y.Z,<X.Y`); it is currently `smythe>=0.8.2,<0.9`. CI installs the
newest smythe release in that range, and verification of a range accepts final
releases only. Widening the range, for example to smythe 0.9, needs an explicit
compatibility check: run the offline suite and the skill smoke test against the
new smythe, then update `runtime.txt`, `SKILL.md`, the README and the
changelog together.

## Per-release flow

1. Confirm that CI passes on `main` for the commit to release: ruff, the
   offline suite on Linux (Python 3.11-3.13) and Windows, and the skill smoke
   test.
2. Set `VERSION` to `X.Y.Z`. Move the `Unreleased` entries in `CHANGELOG.md`
   into a dated `## [X.Y.Z] - YYYY-MM-DD` section, and leave older released
   sections unchanged. Commit and push.
3. Build and verify the archive locally, using fresh directories:

   ```bash
   python tools/skill_archive.py --out /path/to/fresh/skill-dist
   python -m venv /path/to/fresh/runtime
   /path/to/fresh/runtime/bin/python -m pip install -r runtime.txt
   python tools/skill_archive.py --verify /path/to/fresh/skill-dist/repo-doctor-skill-X.Y.Z.zip \
       --python /path/to/fresh/runtime/bin/python
   ```

   The builder reads committed files from one exact commit, ignores untracked
   files and uncommitted changes, and refuses to overwrite an existing archive
   or receipt. Verification checks the inventory and every checksum, checks
   that the interpreter's smythe satisfies `runtime.txt`, and runs the
   extracted skill on an offline fixture with provider keys cleared.
4. Tag `vX.Y.Z` at the version commit and publish a GitHub release for the
   tag. The **Repo Doctor distribution** workflow checks that the tag matches
   `VERSION`, builds and verifies the archive from the tagged commit, and
   attaches the ZIP and receipt. An existing asset name is never replaced.
5. Download the attached assets and confirm that the receipt's `sha256`
   matches the ZIP.

Running the workflow manually builds and verifies a candidate without
publishing a release; the candidate is kept as a workflow artifact.

## If the release workflow fails

- **Tag does not match `VERSION`:** fix `VERSION` in a new commit and release a
  new tag. Never move a published tag.
- **Runtime verification fails:** the newest smythe in the range changed
  behavior. Narrow the range or fix the scripts, with a regression test.
- **Asset already exists:** release assets are never overwritten. Release a
  new version.
