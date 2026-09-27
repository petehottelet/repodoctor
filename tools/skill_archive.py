"""Build a small, immutable Repo Doctor archive and verify it against its smythe runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "README.md", "SKILL.md", "runtime.txt", "references/scoring_rubric.md",
    "scripts/audit_repo.py", "scripts/collect_repo_snapshot.py",
    "evals/evals.json",
    "evals/trigger_evals.json",
    "evals/files/gocli/.github/workflows/ci.yml",
    "evals/files/gocli/LICENSE",
    "evals/files/gocli/README.md",
    "evals/files/gocli/go.mod",
    "evals/files/gocli/main.go",
    "evals/files/gocli/main_test.go",
    "evals/files/hollow/.github/workflows/release.yml",
    "evals/files/hollow/CHANGELOG.md",
    "evals/files/hollow/LICENSE",
    "evals/files/hollow/README.md",
    "evals/files/hollow/SECURITY.md",
    "evals/files/hollow/examples/.gitkeep",
    "evals/files/hollow/pyproject.toml",
    "evals/files/hollow/tests/.gitkeep",
)
LIMIT = 2_000_000

_VERSION = re.compile(r"\d+\.\d+\.\d+(?:[a-z]+\d+)?")
_EXACT = re.compile(r"smythe==(\d+\.\d+\.\d+(?:[a-z]+\d+)?)")
_RANGE = re.compile(r"smythe>=(\d+\.\d+\.\d+),<(\d+\.\d+(?:\.\d+)?)")


def git(root: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=root)


def _release(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def check_runtime(requirement: str) -> str:
    """Accept one exact smythe pin or one bounded range such as smythe>=0.8.2,<0.9."""
    if _EXACT.fullmatch(requirement):
        return requirement
    match = _RANGE.fullmatch(requirement)
    if match and _release(match[1]) < _release(match[2]):
        return requirement
    raise ValueError("Skill runtime must be smythe==X.Y.Z or a bounded range smythe>=X.Y.Z,<X.Y")


def runtime_accepts(requirement: str, installed: str) -> bool:
    """Whether an installed smythe version satisfies the archive's runtime requirement.

    A range accepts final releases only, so a pre-release never passes verification.
    """
    if match := _EXACT.fullmatch(requirement):
        return installed == match[1]
    if (match := _RANGE.fullmatch(requirement)) and re.fullmatch(r"\d+\.\d+\.\d+", installed):
        return _release(match[1]) <= _release(installed) < _release(match[2])
    return False


def build(output: Path, *, ref: str = "HEAD", root: Path = ROOT) -> Path:
    commit = git(root, "rev-parse", "--verify", ref + "^{commit}").decode().strip()
    members = {name: git(root, "show", f"{commit}:{name}") for name in FILES}
    runtime = check_runtime(members["runtime.txt"].decode().strip())
    version = git(root, "show", f"{commit}:VERSION").decode().strip()
    if not _VERSION.fullmatch(version):
        raise ValueError("VERSION must hold one release version, such as 1.2.3")
    members["LICENSE"] = git(root, "show", f"{commit}:LICENSE")
    manifest = {"version": 1, "skill_version": version, "source_commit": commit,
                "runtime": runtime,
                "files": {name: {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                          for name, data in sorted(members.items())}}
    members["MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"repo-doctor-skill-{version}.zip"
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(members.items()):
            info = zipfile.ZipInfo("repo-doctor/" + name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    if path.stat().st_size > LIMIT:
        raise ValueError("Skill archive exceeds 2 MB")
    receipt = {"name": path.name, "bytes": path.stat().st_size,
               "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
               "source_commit": commit, "skill_version": version, "runtime": runtime}
    with (output / (path.stem + ".json")).open("x", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))
    return path


def checked_members(path: Path) -> tuple[dict, dict[str, bytes]]:
    with zipfile.ZipFile(path) as archive:
        expected = {"repo-doctor/" + name for name in (*FILES, "LICENSE", "MANIFEST.json")}
        if set(archive.namelist()) != expected or len(archive.namelist()) != len(expected):
            raise ValueError("Skill archive inventory mismatch")
        if any(info.file_size > LIMIT for info in archive.infolist()):
            raise ValueError("Skill member exceeds size limit")
        members = {name.removeprefix("repo-doctor/"): archive.read(name) for name in expected}
    manifest = json.loads(members.pop("MANIFEST.json"))
    if manifest.get("version") != 1 or set(manifest.get("files", {})) != set(members):
        raise ValueError("Skill manifest inventory mismatch")
    for name, data in members.items():
        if manifest["files"][name] != {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}:
            raise ValueError(f"Skill member checksum mismatch: {name}")
    if manifest["runtime"] != members["runtime.txt"].decode().strip():
        raise ValueError("Skill runtime pin mismatch")
    return manifest, members


def verify(path: Path, python: Path) -> dict:
    # On Unix, venv Python is a symlink; resolving it loses the installed runtime.
    python = python.absolute()
    manifest, members = checked_members(path)
    environment = dict(os.environ)
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY",
                "REPO_DOCTOR_MODEL", "PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    environment["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(prefix="smythe-skill-") as scratch:
        work = Path(scratch)
        installed = subprocess.check_output(
            [str(python), "-I", "-c", "import smythe; print(smythe.__version__)"],
            cwd=work, env=environment, text=True,
        ).strip()
        if not runtime_accepts(manifest["runtime"], installed):
            raise ValueError(
                f"Installed smythe {installed} does not satisfy the archive's runtime "
                f"requirement {manifest['runtime']}"
            )
        for name, data in members.items():
            target = work / "repo-doctor" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        fixture = work / "fixture"
        fixture.mkdir()
        (fixture / "README.md").write_text("# Offline fixture\n", encoding="utf-8")
        (fixture / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1.0.0"\n')
        output = work / "result"
        subprocess.run(
            [str(python), "-I", str(work / "repo-doctor/scripts/audit_repo.py"),
             str(fixture), "--output", str(output)],
            cwd=work, env=environment, check=True, capture_output=True, text=True, timeout=60,
        )
        report = (output / "PROJECT_SCORECARD.md").read_text(encoding="utf-8")
        if "offline (deterministic canned smythe graph)" not in report:
            raise ValueError("Archive verification did not use the offline provider")
        trace = json.loads((output / "trace.json").read_text(encoding="utf-8"))
        if {span["node_id"] for span in trace} != {
            "intake", "packaging", "readme", "testing-ci", "security-license",
            "docs-examples", "red-team", "synthesis",
        }:
            raise ValueError("Offline skill graph did not complete")
        if any(span["status"] != "completed" for span in trace):
            raise ValueError("Offline skill graph contains an incomplete span")
        if not all((output / name).is_file() for name in ("NEXT_STEPS.md", "graph.mmd",
                                                          "snapshot.json")):
            raise ValueError("Missing skill output")
    return {"verified_runtime": installed, "runtime": manifest["runtime"],
            "skill_version": manifest.get("skill_version"),
            "source_commit": manifest["source_commit"],
            "offline_nodes": len(trace), "provider_api_charges_usd": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--python", type=Path)
    args = parser.parse_args()
    if args.out:
        build(args.out, ref=args.ref)
    if args.verify:
        if args.python is None:
            parser.error("--verify requires a clean interpreter with the pinned runtime installed")
        print(json.dumps(verify(args.verify, args.python)))


if __name__ == "__main__":
    main()
