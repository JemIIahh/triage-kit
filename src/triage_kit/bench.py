"""Compare competing pull requests side by side, running each one's tests in a sealed container.

A PR's code is a stranger's code. It runs in Docker with a scratch copy of the
checkout as its only mount, no environment variables and no credentials; the
test phase has no network at all.
"""

import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Literal

from triage_kit.gh import Runner, list_open_prs, run_gh, run_git
from triage_kit.markdown import cell
from triage_kit.models import PullRequest
from triage_kit.sweep import closes_issues

TestStatus = Literal["pass", "fail", "timeout", "error"]

TAIL_LINES = 20
SCOPE_SHOWN = 3
# Exit codes from `docker run` itself, meaning the tests never ran.
DOCKER_ERRORS = (125, 126, 127)

TEST_FILE_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")
SECRET_RE = re.compile(r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY")
LOCKFILES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock", "Pipfile.lock",
    "Cargo.lock", "Gemfile.lock", "go.sum", "composer.lock",
})


@dataclass(frozen=True)
class PrResult:
    number: int
    author: str
    files_changed: int
    additions: int
    deletions: int
    out_of_scope: tuple[str, ...]
    tests: TestStatus
    test_tail: str
    flags: tuple[str, ...]


def prs_for_issue(repo: str, issue: int, run: Runner = run_gh) -> list[PullRequest]:
    return [p for p in list_open_prs(repo, run) if issue in closes_issues(p.body)]


def prepare_worktree(repo_dir: Path, pr: PullRequest, root: Path) -> Path:
    """Check the PR's head out into its own worktree under `root`; a rerun replaces it."""
    path = root / f"pr-{pr.number}"
    # Git won't fetch into a branch that a worktree has checked out, so remove it first.
    if path.exists():
        run_git(["-C", str(repo_dir), "worktree", "remove", "--force", str(path)])
    run_git(["-C", str(repo_dir), "fetch", "--force", "origin", f"+pull/{pr.number}/head:pr-{pr.number}"])
    root.mkdir(parents=True, exist_ok=True)
    run_git(["-C", str(repo_dir), "worktree", "add", "--force", str(path), f"pr-{pr.number}"])
    return path


def diff_stats(worktree: Path, base: str) -> tuple[list[str], int, int]:
    """Files changed since the merge base with `base`, and lines added and deleted."""
    out = run_git(["-C", str(worktree), "diff", "--numstat", "--no-renames", f"{base}...HEAD"])
    files, added, deleted = [], 0, 0
    for line in out.splitlines():
        plus, minus, path = line.split("\t", 2)
        files.append(path)
        # Binary files show "-" for both counts.
        added += int(plus) if plus.isdigit() else 0
        deleted += int(minus) if minus.isdigit() else 0
    return files, added, deleted


def out_of_scope(files: list[str], scope_globs: list[str]) -> tuple[str, ...]:
    return tuple(f for f in files if not any(fnmatchcase(f, g) for g in scope_globs))


def risk_flags(files: list[str], diff_text: str) -> tuple[str, ...]:
    flags = []
    if not any(TEST_FILE_RE.search(f) for f in files):
        flags.append("no tests changed")
    added = "\n".join(line for line in diff_text.splitlines() if line.startswith("+"))
    if SECRET_RE.search(added):
        flags.append("possible secret")
    if any(
        PurePosixPath(f).name in LOCKFILES or f.startswith("dist/") or "/dist/" in f or f.endswith(".min.js")
        for f in files
    ):
        flags.append("generated files")
    return tuple(flags)


def _docker_argv(scratch: Path, image: str, cmd: str, name: str, network: list[str]) -> list[str]:
    return [
        "docker", "run", "--rm", *network,
        "--memory", "1g", "--pids-limit", "256", "--security-opt", "no-new-privileges",
        "--name", name, "-v", f"{scratch}:/work", "-w", "/work",
        image, "sh", "-c", cmd,
    ]


def docker_install_argv(scratch: Path, image: str, cmd: str, name: str) -> list[str]:
    """Installing dependencies may download them, so this phase keeps the network."""
    return _docker_argv(scratch, image, cmd, name, [])


def docker_test_argv(scratch: Path, image: str, cmd: str, name: str) -> list[str]:
    return _docker_argv(scratch, image, cmd, name, ["--network", "none"])


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _contained(argv: list[str], name: str, timeout: int) -> tuple[int | None, str]:
    """Run one container; on timeout remove it and return None as the exit code."""
    try:
        proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        # Killing the docker CLI leaves the container running.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        out = e.stdout.decode(errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        return None, out
    return proc.returncode, proc.stdout


def run_tests(
    worktree: Path, image: str, install_cmd: str | None, test_cmd: str, timeout: int = 300
) -> tuple[TestStatus, str]:
    """Run `test_cmd` against a scratch copy of `worktree`; return the status and the output's last lines."""
    scratch = Path(tempfile.mkdtemp(prefix="issue-desk-"))
    try:
        work = scratch / "work"
        shutil.copytree(worktree, work, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        name = f"issue-desk-{uuid.uuid4().hex[:12]}"
        if install_cmd:
            code, out = _contained(docker_install_argv(work, image, install_cmd, name), name, timeout)
            if code != 0:
                return ("timeout" if code is None else "error"), _tail(out)
        code, out = _contained(docker_test_argv(work, image, test_cmd, name), name, timeout)
        if code is None:
            return "timeout", _tail(out)
        if code in DOCKER_ERRORS:
            return "error", _tail(out)
        return ("pass" if code == 0 else "fail"), _tail(out)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


TEST_LABELS = {"pass": "✅ pass", "fail": "❌ fail", "timeout": "⏱ timeout", "error": "⚠️ error"}


def _scope_cell(files: tuple[str, ...]) -> str:
    if not files:
        return "—"
    shown = ", ".join(files[:SCOPE_SHOWN])
    return shown + (f" +{len(files) - SCOPE_SHOWN} more" if len(files) > SCOPE_SHOWN else "")


def render_comparison(results: list[PrResult]) -> str:
    lines = ["| PR | Author | Files (+/−) | Tests | Out of scope | Flags |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(
            f"| #{r.number} | {cell(r.author)} | {r.files_changed} (+{r.additions}/−{r.deletions}) "
            f"| {TEST_LABELS[r.tests]} | {cell(_scope_cell(r.out_of_scope))} | {', '.join(r.flags) or '—'} |"
        )
    return "\n".join(lines)
