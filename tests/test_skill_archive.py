"""Skill archives are reproducible, self-contained and runtime-bound."""

import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from tools.skill_archive import (
    FILES, ROOT, build, check_runtime, checked_members, runtime_accepts, verify,
)

RUNTIME = "smythe>=0.8.2,<0.9"


def _commit(root, message="Fixture"):
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture" + "@example.invalid",
                    "-c", "commit.gpgsign=false", "commit", "-qm", message], cwd=root, check=True)


@pytest.fixture
def skill_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for name in FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((RUNTIME if name == "runtime.txt" else f"fixture: {name}").encode() + b"\n")
    (root / "LICENSE").write_bytes(b"MIT fixture\n")
    (root / "VERSION").write_bytes(b"0.1.0\n")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # Store the bytes as written on every platform; line-ending checks also slow git add.
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=root, check=True)
    subprocess.run(["git", "add", "--", *FILES, "LICENSE", "VERSION"], cwd=root, check=True)
    _commit(root)
    return root


def test_archive_uses_committed_bytes_and_is_reproducible(skill_repo, tmp_path):
    (skill_repo / "private.txt").write_text("never ship")
    first = build(tmp_path / "one", root=skill_repo)
    (skill_repo / "README.md").write_text("uncommitted change")
    second = build(tmp_path / "two", root=skill_repo)
    assert first.read_bytes() == second.read_bytes()
    assert first.name == "repo-doctor-skill-0.1.0.zip"
    manifest, members = checked_members(first)
    assert manifest["runtime"] == RUNTIME
    assert manifest["skill_version"] == "0.1.0"
    assert len(manifest["source_commit"]) == 40
    assert members["README.md"] == b"fixture: README.md\n"
    assert "LICENSE" in members and "private.txt" not in members


def test_skill_archive_will_not_overwrite_an_existing_release_candidate(skill_repo, tmp_path):
    first = build(tmp_path / "out", root=skill_repo)
    before = first.read_bytes()
    with pytest.raises(FileExistsError):
        build(tmp_path / "out", root=skill_repo)
    assert first.read_bytes() == before


def test_archive_verifier_rejects_modified_content(skill_repo, tmp_path):
    archive = build(tmp_path / "out", root=skill_repo)
    with zipfile.ZipFile(archive) as source:
        members = {name: source.read(name) for name in source.namelist()}
    members["repo-doctor/README.md"] = b"tampered"
    with zipfile.ZipFile(archive, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
    with pytest.raises(ValueError, match="checksum mismatch"):
        checked_members(archive)


def test_runtime_pin_is_verified_before_running_skill_code(skill_repo, tmp_path, monkeypatch):
    archive = build(tmp_path / "out", root=skill_repo)
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: "0.9.0\n")
    with pytest.raises(ValueError, match="does not satisfy"):
        verify(archive, Path("unused-python"))


@pytest.mark.parametrize(
    ("requirement", "installed", "accepted"),
    [
        (RUNTIME, "0.8.2", True),
        (RUNTIME, "0.8.10", True),
        (RUNTIME, "0.8.1", False),
        (RUNTIME, "0.9.0", False),
        (RUNTIME, "0.8.3rc1", False),
        ("smythe==0.8.2", "0.8.2", True),
        ("smythe==0.8.2", "0.8.3", False),
    ],
)
def test_runtime_requirement_accepts_only_matching_releases(requirement, installed, accepted):
    assert runtime_accepts(requirement, installed) is accepted


@pytest.mark.parametrize(
    "requirement", ["smythe", "smythe>=0.8.2", "smythe>=0.9.0,<0.9", "smythe~=0.8.2"],
)
def test_runtime_must_be_an_exact_pin_or_a_bounded_range(requirement):
    with pytest.raises(ValueError, match="Skill runtime must be"):
        check_runtime(requirement)


def test_build_refuses_an_unbounded_committed_runtime(skill_repo, tmp_path):
    (skill_repo / "runtime.txt").write_bytes(b"smythe>=0.8.2\n")
    subprocess.run(["git", "add", "--", "runtime.txt"], cwd=skill_repo, check=True)
    _commit(skill_repo, "Change runtime")
    with pytest.raises(ValueError, match="Skill runtime must be"):
        build(tmp_path / "out", root=skill_repo)


def test_external_receipt_hashes_the_actual_archive(skill_repo, tmp_path):
    import hashlib

    archive = build(tmp_path / "out", root=skill_repo)
    receipt = json.loads(archive.with_suffix(".json").read_text())
    assert receipt["bytes"] == archive.stat().st_size
    assert receipt["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert receipt["skill_version"] == "0.1.0"


def test_inventory_lists_every_tracked_skill_file():
    tracked = subprocess.check_output(
        ["git", "ls-files", "-z", "--", "README.md", "SKILL.md", "runtime.txt",
         "references", "scripts", "evals"],
        cwd=ROOT,
    ).decode().split("\0")
    assert set(tracked) - {""} == set(FILES)


def test_repository_archive_ships_the_skill_and_its_evals(tmp_path):
    archive = build(tmp_path / "out")
    manifest, members = checked_members(archive)
    assert manifest["runtime"] == (ROOT / "runtime.txt").read_text().strip()
    assert json.loads(members["evals/evals.json"])["skill_name"] == "repo-doctor"
    assert members["evals/files/hollow/README.md"] == b""


def test_verification_preserves_the_virtual_environment_interpreter(skill_repo, tmp_path, monkeypatch):
    import os
    import venv

    archive = build(tmp_path / "out", root=skill_repo)
    environment = tmp_path / "isolated-runtime"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    original_run = subprocess.run

    class InterpreterChecked(Exception):
        pass

    def inspect_interpreter(command, **kwargs):
        actual = original_run(
            [command[0], "-I", "-c", "import sys; print(sys.prefix)"],
            cwd=kwargs["cwd"], env=kwargs["env"], capture_output=True, text=True, check=True,
        )
        assert Path(actual.stdout.strip()).resolve() == environment.resolve()
        raise InterpreterChecked

    monkeypatch.setattr(subprocess, "check_output", inspect_interpreter)
    with pytest.raises(InterpreterChecked):
        verify(archive, python)
