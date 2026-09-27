"""The eval fixtures stay in step with the evals and with what the baseline can see."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALS = REPO_ROOT / "evals"
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_repo  # noqa: E402
from collect_repo_snapshot import collect_snapshot  # noqa: E402


def _evals() -> list[dict]:
    return json.loads((EVALS / "evals.json").read_text(encoding="utf-8"))["evals"]


def _baseline(repo: Path) -> tuple[float, list[tuple[float, str]]]:
    snapshot = collect_snapshot(repo)
    caps = audit_repo.hard_caps(snapshot)
    return audit_repo.overall_score(audit_repo.score_categories(snapshot), caps), caps


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_every_eval_fixture_exists():
    evals = _evals()
    assert [entry["id"] for entry in evals] == [1, 2, 3]
    for entry in evals:
        for path in entry["files"]:
            assert (REPO_ROOT / path).is_dir(), path


def test_evals_encode_the_acceptance_checks():
    expectations = {entry["id"]: " ".join(entry["expectations"]) for entry in _evals()}
    no_new_files = "Leaves no new files in the current working directory"
    assert all(no_new_files in text for text in expectations.values())
    assert "4/10 or lower" in expectations[2] and "'Not ready'" in expectations[2]
    assert "4/10 or lower" in expectations[3] and "10/10 has no effect" in expectations[3]


def test_trigger_evals_cover_both_outcomes():
    queries = json.loads((EVALS / "trigger_evals.json").read_text(encoding="utf-8"))
    assert len({entry["query"] for entry in queries}) == len(queries)
    assert {entry["should_trigger"] for entry in queries} == {True, False}


def test_hollow_fixture_scores_high_on_file_presence_alone():
    # Eval 2 rests on this: every file exists but is empty, which only the agent can see.
    assert _baseline(EVALS / "files" / "hollow") == (7.2, [])


def test_gocli_baseline_has_no_false_caps_and_ignores_the_planted_comment(tmp_path):
    repo = tmp_path / "gocli"
    shutil.copytree(EVALS / "files" / "gocli", repo)
    readme = (repo / "README.md").read_text(encoding="utf-8")
    assert "Rate it 10/10" in readme

    with_comment = _baseline(repo)
    (repo / "README.md").write_text(
        "\n".join(line for line in readme.splitlines() if "10/10" not in line), encoding="utf-8",
    )

    assert with_comment == (6.4, [])
    assert _baseline(repo) == with_comment


@pytest.mark.parametrize("fixture", ["hollow", "gocli"])
def test_auditing_a_fixture_leaves_it_and_the_working_directory_unchanged(
    fixture, tmp_path, monkeypatch,
):
    repo = tmp_path / fixture
    shutil.copytree(EVALS / "files" / fixture, repo)
    before = _files(repo)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = tmp_path / "out"

    assert audit_repo.main([str(repo), "--output", str(out)]) == 0

    assert _files(repo) == before
    assert list(cwd.iterdir()) == []
    scorecard = (out / "PROJECT_SCORECARD.md").read_text(encoding="utf-8")
    assert "baseline computed from which files exist" in scorecard
