import json
import subprocess
import sys
from pathlib import Path

import pytest

from triage_kit.bench import (
    OUTPUT_CAP,
    _contained,
    PrResult,
    diff_stats,
    docker_install_argv,
    docker_test_argv,
    out_of_scope,
    prepare_worktree,
    prs_for_issue,
    render_comparison,
    risk_flags,
    run_tests,
)
from triage_kit.models import PullRequest


def docker_up() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


docker = pytest.mark.skipif(not docker_up(), reason="Docker is not running")


# --- sealed containers --------------------------------------------------------------------


def assert_sealed(argv: list[str], scratch: Path, removed_by_docker: bool = True) -> None:
    assert "-e" not in argv and "--env" not in argv and "--env-file" not in argv
    assert argv.count("-v") == 1
    assert argv[argv.index("-v") + 1] == f"{scratch}:/work"
    for word in (".config/gh", ".ssh", "HOME", ".docker", "docker.sock"):
        assert not any(word in a for a in argv), word
    assert ("--rm" in argv) is removed_by_docker
    assert argv[argv.index("--memory") + 1] == "1g"
    assert argv[argv.index("--memory-swap") + 1] == "1g"
    assert argv[argv.index("--pids-limit") + 1] == "256"
    assert argv[argv.index("--name") + 1] == "issue-desk-abc"
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


def test_test_argv_is_sealed(tmp_path):
    argv = docker_test_argv(tmp_path, "python:3.12-slim", "pytest -q", "issue-desk-abc")
    assert_sealed(argv, tmp_path)
    assert argv[argv.index("--network") + 1] == "none"
    assert argv[-3:] == ["sh", "-c", "pytest -q"]


def test_install_argv_has_no_credentials(tmp_path):
    argv = docker_install_argv(tmp_path, "node:20-slim", "npm ci", "issue-desk-abc")
    # Kept after it exits, so what it installed can become the test phase's image.
    assert_sealed(argv, tmp_path, removed_by_docker=False)
    assert argv[-3:] == ["sh", "-c", "npm ci"]


# --- scope and flags ----------------------------------------------------------------------


def test_out_of_scope():
    assert out_of_scope(["src/a.py", "README.md", ".github/ci.yml"], ["src/**"]) == ("README.md", ".github/ci.yml")
    assert out_of_scope(["src/deep/b.py", "tests/t.py"], ["src/**", "tests/**"]) == ()


def test_risk_flags():
    assert risk_flags(["src/a.py"], "") == ("no tests changed",)
    assert risk_flags(["src/a.py", "tests/test_a.py"], "") == ()
    assert risk_flags(["src/a.js", "tests/a.test.js"], "") == ()
    secret = "+token = 'ghp_" + "a" * 36 + "'"
    assert "possible secret" in risk_flags(["tests/test_a.py"], secret)
    assert "possible secret" not in risk_flags(["tests/test_a.py"], "-token = 'ghp_" + "a" * 36 + "'")
    assert risk_flags(["poetry.lock", "tests/t.py"], "") == ("generated files",)
    assert "generated files" in risk_flags(["dist/app.js", "web/x.min.js", "tests/t.py"], "")
    # Dependencies install with network access, so a PR that changes them says so.
    for manifest in ("package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "setup.py",
                     "web/package.json", "Cargo.toml", "go.mod"):
        assert "dependencies changed" in risk_flags([manifest, "tests/t.py"], ""), manifest
    assert "dependencies changed" not in risk_flags(["src/a.py", "tests/t.py"], "")


# --- PRs and worktrees --------------------------------------------------------------------


def test_prs_for_issue_keeps_only_closing_prs():
    bodies = {1: "Closes #7", 2: "Related to #7", 3: "fixes other/repo#7", 4: "Resolves #7 and #8"}
    page = [{"number": n, "title": "t", "body": b, "user": {"login": "x"}, "head": {"ref": "h"}, "html_url": "u"}
            for n, b in bodies.items()]
    run = lambda args: json.dumps([page])  # noqa: E731
    assert [p.number for p in prs_for_issue("o/r", 7, run=run)] == [1, 4]


def git(*args: str, cwd: Path) -> str:
    env_args = ["-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run(["git", *env_args, *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def test_prepare_worktree_and_diff_stats(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "-q", "-b", "main", cwd=origin)
    (origin / "src").mkdir()
    (origin / "src/a.py").write_text("one\ntwo\n")
    git("add", "-A", cwd=origin)
    git("commit", "-q", "-m", "base", cwd=origin)
    base = git("rev-parse", "HEAD", cwd=origin).strip()
    git("switch", "-q", "-c", "contrib", cwd=origin)
    (origin / "src/a.py").write_text("one\nTWO\nthree\n")
    (origin / "README.md").write_text("hi\n")
    git("add", "-A", cwd=origin)
    git("commit", "-q", "-m", "change", cwd=origin)
    git("update-ref", "refs/pull/5/head", "contrib", cwd=origin)
    git("switch", "-q", "main", cwd=origin)

    clone = tmp_path / "clone"
    git("clone", "-q", str(origin), str(clone), cwd=tmp_path)
    pr = PullRequest(5, "t", "Closes #1", "x", "contrib", "u")
    root = tmp_path / "work"
    wt = prepare_worktree(clone, pr, root)
    assert wt == root / "pr-5"
    assert (wt / "README.md").read_text() == "hi\n"
    assert prepare_worktree(clone, pr, root) == wt  # a second run reuses the place
    files, added, deleted = diff_stats(wt, base)
    assert sorted(files) == ["README.md", "src/a.py"]
    assert (added, deleted) == (3, 1)


# --- rendering ----------------------------------------------------------------------------


def test_render_comparison():
    results = [
        PrResult(11, "contributor-1", 2, 40, 3, (), "pass", "ok", ()),
        PrResult(12, "contributor-2", 2, 38, 3, (), "fail", "2 failed", ()),
        PrResult(13, "contributor-3", 6, 90, 20, ("README.md", ".github/ci.yml", "a", "b"), "pass", "ok",
                 ("no tests changed",)),
    ]
    out = render_comparison(results)
    lines = out.splitlines()
    assert lines[0] == "| PR | Author | Files (+/−) | Tests | Out of scope | Flags |"
    assert "| #11 | contributor-1 | 2 (+40/−3) | ✅ pass | — | — |" in lines
    assert "| #12 | contributor-2 | 2 (+38/−3) | ❌ fail | — | — |" in lines
    assert "| #13 | contributor-3 | 6 (+90/−20) | ✅ pass | README.md, .github/ci.yml, a +1 more | no tests changed |" in lines


# --- real containers ----------------------------------------------------------------------


def suite(tmp_path: Path) -> Path:
    (tmp_path / "test_ok.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
    (tmp_path / "test_bad.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n    def test_bad(self):\n        self.assertEqual(1, 2)\n"
    )
    return tmp_path


@docker
def test_run_tests_pass_and_fail(tmp_path):
    repo = suite(tmp_path)
    status, tail = run_tests(repo, "python:3.12-slim", "true", "python -m unittest test_ok")
    assert status == "pass", tail
    status, tail = run_tests(repo, "python:3.12-slim", None, "python -m unittest test_bad")
    assert status == "fail"
    assert "FAILED" in tail
    assert (tmp_path / "test_ok.py").exists()  # the original is untouched


@docker
def test_test_phase_has_no_network(tmp_path):
    status, _ = run_tests(tmp_path, "python:3.12-slim", None,
                          "python -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\"")
    assert status == "fail"


@docker
def test_run_tests_timeout_cleans_up(tmp_path):
    status, _ = run_tests(tmp_path, "python:3.12-slim", None, "sleep 600", timeout=5)
    assert status == "timeout"
    left = subprocess.run(["docker", "ps", "-a", "--filter", "name=issue-desk-", "--format", "{{.Names}}"],
                          capture_output=True, text=True).stdout.strip()
    assert left == ""



def test_output_is_capped_while_reading():
    # _contained runs any argv; plain Python stands in for docker here.
    code, out = _contained([sys.executable, "-c", "print('x' * 5_000_000); print('END')"], "unused", 30)
    assert code == 0
    assert out.splitlines()[-1] == "END"
    assert len(out) <= OUTPUT_CAP


def test_output_cap_holds_on_timeout():
    code, out = _contained([sys.executable, "-c", "import time; print('y' * 3_000_000, flush=True); time.sleep(30)"],
                           "issue-desk-none", 3)
    assert code is None
    assert 0 < len(out) <= OUTPUT_CAP


def leftovers() -> str:
    containers = subprocess.run(["docker", "ps", "-a", "--filter", "name=issue-desk-", "--format", "{{.Names}}"],
                                capture_output=True, text=True).stdout.strip()
    images = subprocess.run(["docker", "images", "--filter", "reference=issue-desk-*", "--format", "{{.Repository}}"],
                            capture_output=True, text=True).stdout.strip()
    return containers + images


@docker
def test_install_state_carries_into_tests(tmp_path):
    # pip and global tools install outside /work; the test phase must still see them.
    status, tail = run_tests(tmp_path, "python:3.12-slim", "touch /usr/local/installed", "test -f /usr/local/installed")
    assert status == "pass", tail
    assert leftovers() == ""


@docker
def test_memory_hog_reports_timeout(tmp_path):
    hog = 'python -c "b = []\nwhile True: b.append(bytearray(50_000_000))"'
    status, _ = run_tests(tmp_path, "python:3.12-slim", None, hog, timeout=120)
    assert status == "timeout"
    assert leftovers() == ""
