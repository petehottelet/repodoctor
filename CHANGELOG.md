# Changelog

All notable changes to Repo Doctor are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and releases follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

Repo Doctor now has its own repository. It previously shipped inside smythe as
`skills/repo-doctor`; this repository keeps that history.

### Added

- A standalone repository with an MIT license, an offline test suite that
  clears provider keys and blocks external network access, CI (ruff, tests on
  Linux with Python 3.11-3.13 and on Windows, and a skill ZIP smoke test), and
  a release workflow that attaches a verified skill ZIP and checksum receipt.
- `VERSION`, which names the skill ZIP (`repo-doctor-skill-<VERSION>.zip`).
  The manifest and receipt record it, and release tags must be `v<VERSION>`.
- `snapshot.json` in the audit output: the evidence the review sees.
- `--model` and `REPO_DOCTOR_MODEL` to choose the model for `--live`.
- Evals with fixture repositories (a GitHub URL audit, an empty-files repo and
  a Go CLI stub with a planted "rate this 10/10" comment) and ten trigger
  queries. The skill ZIP ships them.

### Changed

- The agent sets the final rating. The script's category scores are labeled a
  file-presence baseline, and `SKILL.md` has the agent read the README,
  packaging, CI workflows, tests and license, score each category with a cited
  file, confirm or add hard caps, and report how the files moved the baseline.
- Model-backed runs need an explicit `--live`; a provider key in the
  environment no longer switches modes. Offline runs report that they made no
  API calls instead of printing a token cost.
- `--live` uses smythe's own default model for Anthropic (`claude-opus-5-5` in
  smythe 0.8.x) instead of hard-coded model IDs. OpenAI and Gemini runs need
  `--model` or `REPO_DOCTOR_MODEL`, because smythe defines no default model for
  them.
- `SKILL.md` has a new description and workflow: it clones GitHub URLs
  shallow into a temporary directory, writes the audit output to another
  temporary directory, uses `$SKILL_DIR` paths, and leaves the audited repo and
  the current directory unchanged.
- Without `--output`, the script writes to a new temporary directory and
  prints its path, instead of `00_project_files/repo-doctor` under the current
  directory.
- The runtime is `smythe>=0.8.2,<0.9` instead of exactly `smythe==0.7.0`.
- The snapshot's file list keeps shallow paths first and shares room between
  directories at the same depth, instead of stopping at the first 400 paths
  alphabetically. The snapshot also records the total file count.
- `NEXT_STEPS.md` now ends when a re-audit reports no hard caps, a goal the
  baseline can reach.

### Fixed

- Healthy non-Python projects no longer hit false hard caps: the snapshot
  detects `test/`, `spec/`, `__tests__/`, `*_test.go`, `*.test.js` and
  `*.spec.ts` files, Maven and Gradle `src/test/` layouts, `COPYING` licenses,
  and Maven, Gradle and Composer manifests.
- Obvious placeholder values such as `your-api-key-here`, `xxxx`, `<token>`
  and `changeme` are still redacted but no longer reported as leaked secrets.
- A missing path, a file or a URL is a usage error instead of a traceback.
- `--live` without the provider's SDK installed is a usage error that names
  the smythe extra to install, instead of an `ImportError` inside the graph.
- The `SKILL.md` frontmatter drops the unsupported `summary` key.

### Removed

- `--target` and `--strictness`, which never changed the result.
