"""SKILL.md stays loadable and keeps the workflow's safety rules."""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

SKILL = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")


def test_frontmatter_has_only_supported_keys():
    match = re.match(r"---\n(.*?)\n---\n", SKILL, re.S)
    assert match, "SKILL.md must start with YAML frontmatter"
    frontmatter = yaml.safe_load(match[1])
    assert set(frontmatter) == {"name", "description"}
    assert frontmatter["name"] == "repo-doctor"
    assert len(frontmatter["description"]) <= 1024
    assert "<" not in frontmatter["description"] and ">" not in frontmatter["description"]


def test_workflow_keeps_output_out_of_the_audited_repo():
    assert 'python "$SKILL_DIR/scripts/audit_repo.py" <repo> --output "$(mktemp -d)"' in SKILL
    assert 'git clone --depth 1 <url> "$(mktemp -d)/repo"' in SKILL
    assert "00_project_files" not in SKILL


def test_agent_sets_the_rating_and_live_runs_need_a_request():
    assert "you make the judgment and set the final rating" in SKILL
    assert "Never relay its number" in SKILL
    assert "Don't pass `--live` unless the user asks" in SKILL
