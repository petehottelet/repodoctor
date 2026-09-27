"""Tests for the Repo Doctor skill: snapshot collector and offline audit."""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest
import smythe

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import audit_repo  # noqa: E402
import collect_repo_snapshot  # noqa: E402
from collect_repo_snapshot import collect_snapshot  # noqa: E402

FAKE_TOKEN = "ghp_" + "a1B2c3D4e5" * 4  # planted GitHub-token shape, 40 chars
# Placeholder keys are assembled at runtime so the source never holds a key-shaped literal.
PLACEHOLDER_OPENAI_KEY = "sk-" + "your-api-key-here-0000000000"


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """A tiny Python package with one planted secret and no license/CI."""
    repo = tmp_path / "tinypkg"
    (repo / "src" / "tinypkg").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "tinypkg"\nversion = "0.1.0"\n', encoding="utf-8",
    )
    (repo / "README.md").write_text(
        f"# tinypkg\n\nA tiny package.\n\nDebug token: {FAKE_TOKEN}\n", encoding="utf-8",
    )
    (repo / "src" / "tinypkg" / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
    (repo / "tests" / "test_basic.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / ".env").write_text(f"GITHUB_TOKEN={FAKE_TOKEN}\n", encoding="utf-8")
    return repo


def test_snapshot_schema_and_signals(fixture_repo: Path) -> None:
    snapshot = collect_snapshot(fixture_repo)

    assert set(snapshot) == {"repo", "detected", "files", "signals", "risks", "limits"}
    assert snapshot["repo"]["name"] == "tinypkg"
    assert snapshot["detected"]["ecosystems"] == ["python"]
    assert snapshot["detected"]["release_targets"] == ["pypi"]
    assert "pyproject.toml" in snapshot["files"]["important_files"]
    assert "src/tinypkg/__init__.py" in snapshot["files"]["tree_summary"]

    signals = snapshot["signals"]
    assert signals["has_tests"] is True
    assert signals["has_license"] is False
    assert signals["has_ci"] is False
    assert signals["has_examples"] is False
    assert snapshot["limits"]["files_read"] >= 2


def test_snapshot_redacts_planted_secret(fixture_repo: Path) -> None:
    snapshot = collect_snapshot(fixture_repo)
    serialized = json.dumps(snapshot)

    assert FAKE_TOKEN not in serialized
    assert "[REDACTED]" in snapshot["files"]["important_files"]["README.md"]
    suspect_files = {s["file"] for s in snapshot["risks"]["secret_suspects"]}
    assert {"README.md", ".env"} <= suspect_files
    for suspect in snapshot["risks"]["secret_suspects"]:
        assert suspect["redacted"] == "ghp_***"


def test_snapshot_is_deterministic(fixture_repo: Path) -> None:
    assert collect_snapshot(fixture_repo) == collect_snapshot(fixture_repo)


def test_snapshot_never_executes_repo_code(fixture_repo: Path) -> None:
    (fixture_repo / "setup.py").write_text(
        "raise SystemExit('snapshot executed repo code')\n", encoding="utf-8",
    )
    snapshot = collect_snapshot(fixture_repo)  # must not raise
    assert "setup.py" in snapshot["files"]["important_files"]


def test_hard_caps_bound_fixture_score(fixture_repo: Path) -> None:
    snapshot = collect_snapshot(fixture_repo)
    scores = audit_repo.score_categories(snapshot)
    caps = audit_repo.hard_caps(snapshot)
    overall = audit_repo.overall_score(scores, caps)

    assert any("license" in reason.lower() for _, reason in caps)
    assert overall <= 6.0  # no-license cap
    assert all(1 <= score <= 10 for score, _ in scores.values())


def test_offline_audit_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    out = tmp_path / "audit-output"

    exit_code = audit_repo.main([str(REPO_ROOT), "--output", str(out)])

    assert exit_code == 0
    for name in ("PROJECT_SCORECARD.md", "NEXT_STEPS.md", "graph.mmd", "trace.json"):
        assert (out / name).is_file(), f"missing {name}"

    scorecard = (out / "PROJECT_SCORECARD.md").read_text(encoding="utf-8")
    assert f"# Project Audit: {REPO_ROOT.name}" in scorecard
    assert "## Overall Rating" in scorecard
    assert "/10**" in scorecard
    assert "| Tests and CI |" in scorecard

    graph = (out / "graph.mmd").read_text(encoding="utf-8")
    assert "flowchart TD" in graph
    assert "synthesis" in graph

    trace = json.loads((out / "trace.json").read_text(encoding="utf-8"))
    node_ids = {span["node_id"] for span in trace}
    assert {"intake", "red-team", "synthesis"} <= node_ids

    next_steps = (out / "NEXT_STEPS.md").read_text(encoding="utf-8")
    assert next_steps.startswith("# Next Steps")
    assert "## Done When" in next_steps


def test_default_audit_output_goes_to_a_new_temporary_directory(fixture_repo, tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    work, temp = tmp_path / "work", tmp_path / "temp"
    work.mkdir()
    temp.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setattr(audit_repo.tempfile, "tempdir", str(temp))
    before = sorted(p.name for p in fixture_repo.rglob("*"))
    assert audit_repo.main([str(fixture_repo)]) == 0
    [out] = temp.iterdir()
    assert out.name.startswith("repo-doctor-")
    assert (out / "PROJECT_SCORECARD.md").is_file()
    assert not any(work.iterdir())
    assert sorted(p.name for p in fixture_repo.rglob("*")) == before


def test_snapshot_never_traverses_private_project_materials(fixture_repo, monkeypatch):
    before = collect_snapshot(fixture_repo)
    private = fixture_repo / "00_project_files"
    private.mkdir()
    (private / "credentials.txt").write_text(FAKE_TOKEN)
    original = Path.iterdir

    def guarded(directory):
        if directory == private:
            pytest.fail("Private working material was traversed")
        return original(directory)

    monkeypatch.setattr(Path, "iterdir", guarded)
    assert collect_snapshot(fixture_repo) == before


@pytest.mark.parametrize(
    ("files", "signal"),
    [
        ({"go.mod": "module x\n", "main_test.go": "package main\n"}, "has_tests"),
        ({"package.json": "{}", "test/router.test.js": ""}, "has_tests"),
        ({"pom.xml": "<project/>", "src/test/java/ATest.java": ""}, "has_tests"),
        ({"COPYING": "GNU GENERAL PUBLIC LICENSE\n"}, "has_license"),
    ],
)
def test_snapshot_detects_common_non_python_layouts(tmp_path, files, signal):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text, encoding="utf-8")
    assert collect_snapshot(tmp_path)["signals"][signal] is True


def test_maven_project_counts_as_packaged(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    snapshot = collect_snapshot(tmp_path)
    assert "java" in snapshot["detected"]["ecosystems"]
    assert not any("packaging" in reason for _, reason in audit_repo.hard_caps(snapshot))


def test_api_key_alone_keeps_audit_offline(fixture_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    out = tmp_path / "out"
    assert audit_repo.main([str(fixture_repo), "--output", str(out)]) == 0
    scorecard = (out / "PROJECT_SCORECARD.md").read_text(encoding="utf-8")
    assert "offline (deterministic canned smythe graph)" in scorecard
    snapshot = json.loads((out / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["repo"]["name"] == "tinypkg"


def test_live_without_api_key_is_a_usage_error(fixture_repo, tmp_path, monkeypatch):
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(SystemExit) as excinfo:
        audit_repo.main([str(fixture_repo), "--live", "--output", str(tmp_path / "out")])
    assert excinfo.value.code == 2


def _write(root: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")


def test_tree_summary_keeps_shallow_paths_past_a_large_subtree(tmp_path):
    # The list used to be the first 400 paths alphabetically, so a large
    # benchmarks/ tree hid the source and tests entirely.
    _write(tmp_path, {f"benchmarks/records/run_{i:03d}.json": "{}" for i in range(450)})
    _write(tmp_path, {"README.md": "# demo\n", "src/demo/__init__.py": "", "tests/test_demo.py": ""})

    snapshot = collect_snapshot(tmp_path)
    tree = snapshot["files"]["tree_summary"]

    assert len(tree) == 400 and tree == sorted(tree)
    assert {"README.md", "src/demo/__init__.py", "tests/test_demo.py"} <= set(tree)
    assert snapshot["limits"]["files_total"] == 453
    assert snapshot["limits"]["truncated"] is True


def test_tree_summary_shares_room_between_directories_at_one_depth(tmp_path):
    _write(tmp_path, {f"aaa/{i:02d}.txt": "" for i in range(30)})
    _write(tmp_path, {f"zzz/{i:02d}.txt": "" for i in range(30)})
    _write(tmp_path, {"top.txt": ""})

    tree = collect_snapshot(tmp_path, max_tree_entries=11)["files"]["tree_summary"]

    assert "top.txt" in tree
    assert sum(p.startswith("aaa/") for p in tree) == sum(p.startswith("zzz/") for p in tree) == 5


@pytest.mark.parametrize(
    "line",
    [
        "OPENAI_API_KEY=" + PLACEHOLDER_OPENAI_KEY,
        "ANTHROPIC_API_KEY=sk-" + "ant-xxxxxxxxxxxxxxxxxxxxxxxx",
        "GITHUB_TOKEN=ghp_" + "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "AWS_ACCESS_KEY_ID=AKIA" + "IOSFODNN7EXAMPLE",
        'api_key = "<api-token-value>"',
        'password = "changeme-' + 'before-deploy"',
        'token: "${SERVICE_TOKEN_VALUE}"',
    ],
)
def test_placeholder_secrets_are_redacted_but_not_reported(tmp_path, line):
    _write(tmp_path, {".env.example": line + "\n", "README.md": f"# demo\n\n    {line}\n"})

    snapshot = collect_snapshot(tmp_path)

    assert snapshot["risks"]["secret_suspects"] == []
    assert "[REDACTED]" in snapshot["files"]["important_files"]["README.md"]


def test_real_secret_beside_placeholders_is_still_reported(tmp_path):
    _write(tmp_path, {".env.example": (
        f"OPENAI_API_KEY={PLACEHOLDER_OPENAI_KEY}\n"
        f"GITHUB_TOKEN={FAKE_TOKEN}\n"
    )})

    suspects = collect_snapshot(tmp_path)["risks"]["secret_suspects"]

    assert suspects == [{"file": ".env.example", "redacted": "ghp_***"}]


def test_placeholder_env_example_keeps_the_hygiene_score(tmp_path):
    _write(tmp_path, {
        "LICENSE": "MIT License\n",
        "SECURITY.md": "Report vulnerabilities privately.\n",
        ".env.example": f"OPENAI_API_KEY={PLACEHOLDER_OPENAI_KEY}\n",
    })

    scores = audit_repo.score_categories(collect_snapshot(tmp_path))

    assert scores["Security/license/community hygiene"][0] == 8


@pytest.mark.parametrize("entrypoint", [audit_repo.main, collect_repo_snapshot.main])
@pytest.mark.parametrize(
    ("make_path", "message"),
    [
        (lambda tmp: str(tmp / "no-such-repo"), "no such directory"),
        (lambda tmp: str(tmp / "file.txt"), "not a directory"),
        (lambda tmp: "https://github.com/petehottelet/smythe", "git clone --depth 1"),
    ],
)
def test_unusable_path_is_a_usage_error_not_a_traceback(
    entrypoint, make_path, message, tmp_path, monkeypatch, capsys,
):
    (tmp_path / "file.txt").write_text("not a repo", encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)

    with pytest.raises(SystemExit) as excinfo:
        entrypoint([make_path(tmp_path)])

    assert excinfo.value.code == 2
    assert message in capsys.readouterr().err
    assert list(cwd.iterdir()) == []


@pytest.mark.parametrize("option", ["--target", "--strictness"])
def test_options_that_changed_nothing_are_rejected(option, fixture_repo, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        audit_repo.main([str(fixture_repo), option, "pypi", "--output", str(tmp_path / "out")])
    assert excinfo.value.code == 2


def test_live_anthropic_run_uses_smythes_default_model(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_sdk_installed", lambda module: True)

    provider, model = audit_repo.live_provider()

    assert isinstance(provider, audit_repo.AnthropicProvider)
    assert model == inspect.signature(smythe.Swarm).parameters["model"].default


def test_live_model_follows_smythe_when_its_default_changes(monkeypatch):
    class FutureSwarm:
        def __init__(self, model: str = "claude-future-1") -> None:
            self.model = model

    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_sdk_installed", lambda module: True)
    monkeypatch.setattr(audit_repo, "Swarm", FutureSwarm)

    assert audit_repo.live_provider()[1] == "claude-future-1"


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "GOOGLE_API_KEY"])
def test_live_run_without_a_smythe_default_needs_a_model(key, fixture_repo, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(key, "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_sdk_installed", lambda module: True)

    with pytest.raises(SystemExit) as excinfo:
        audit_repo.main([str(fixture_repo), "--live", "--output", str(tmp_path / "out")])

    assert excinfo.value.code == 2
    assert "needs --model or REPO_DOCTOR_MODEL" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_model_option_selects_the_live_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_sdk_installed", lambda module: True)

    provider, model = audit_repo.live_provider("gpt-test-model")

    assert isinstance(provider, audit_repo.OpenAIProvider)
    assert model == "gpt-test-model"


def test_model_environment_variable_reaches_the_live_provider(fixture_repo, monkeypatch):
    monkeypatch.setenv("REPO_DOCTOR_MODEL", "env-model")
    seen = []

    def stop(model=None):
        seen.append(model)
        raise ValueError("stopped before any provider call")

    monkeypatch.setattr(audit_repo, "live_provider", stop)
    with pytest.raises(SystemExit):
        audit_repo.main([str(fixture_repo), "--live"])
    assert seen == ["env-model"]


def test_model_without_live_is_a_usage_error(fixture_repo, tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        audit_repo.main([str(fixture_repo), "--model", "any", "--output", str(tmp_path / "out")])
    assert excinfo.value.code == 2


def test_live_run_without_the_provider_sdk_is_a_usage_error(fixture_repo, tmp_path, monkeypatch, capsys):
    # This used to fail inside the graph with an ImportError traceback.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_sdk_installed", lambda module: False)

    with pytest.raises(SystemExit) as excinfo:
        audit_repo.main([str(fixture_repo), "--live", "--output", str(tmp_path / "out")])

    assert excinfo.value.code == 2
    assert f'pip install "smythe[anthropic]=={smythe.__version__}"' in capsys.readouterr().err


def test_api_keys_alone_never_construct_a_live_provider(fixture_repo, tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a live provider was constructed without --live")

    for key, _, module, extra in audit_repo._LIVE_PROVIDERS:
        monkeypatch.setenv(key, "not-a-real-key")
    monkeypatch.setattr(audit_repo, "_LIVE_PROVIDERS", tuple(
        (key, refuse, module, extra) for key, _, module, extra in audit_repo._LIVE_PROVIDERS
    ))

    assert audit_repo.main([str(fixture_repo), "--output", str(tmp_path / "out")]) == 0


def test_review_task_treats_repository_contents_as_untrusted(fixture_repo):
    task = audit_repo.build_task(collect_snapshot(fixture_repo))

    assert any("untrusted data" in rule and "ignore" in rule for rule in task.constraints)
    assert FAKE_TOKEN not in task.goal


def test_audit_leaves_the_repo_and_working_directory_unchanged(fixture_repo, tmp_path, monkeypatch):
    def files(root: Path) -> dict[str, bytes]:
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    before = files(fixture_repo)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    out = tmp_path / "out"

    assert audit_repo.main([str(fixture_repo), "--output", str(out)]) == 0

    assert files(fixture_repo) == before
    assert list(cwd.iterdir()) == []
    assert sorted(p.name for p in out.iterdir()) == [
        "NEXT_STEPS.md", "PROJECT_SCORECARD.md", "graph.mmd", "snapshot.json", "trace.json",
    ]
