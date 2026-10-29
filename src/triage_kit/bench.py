"""Compare competing pull requests side by side, running each one's tests in a sealed container.

A PR's code is a stranger's code. It runs in Docker with a scratch copy of the
checkout as its only mount, no environment variables and no credentials; the
test phase has no network at all. Code that would phone home or read credentials
is flagged too, since tests that pass don't show it.
"""

import re
import shutil
import subprocess
import tempfile
import threading
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
# Bytes of output kept per container while reading, however much a PR prints.
OUTPUT_CAP = 64 * 1024
SCOPE_SHOWN = 3
# Exit codes from `docker run` itself, meaning the tests never ran.
DOCKER_ERRORS = (125, 126, 127)
# SIGKILL: the memory limit's OOM killer. Reported like a timeout, as a limit the PR hit.
KILLED = 137

TEST_FILE_RE = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*$|_test\.\w+$|\.(test|spec)\.\w+$")
SECRET_RE = re.compile(r"AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|-----BEGIN [A-Z ]*PRIVATE KEY")
MANIFEST_RE = re.compile(
    r"(^|/)(package\.json|requirements[^/]*\.txt|pyproject\.toml|setup\.py|setup\.cfg|Pipfile|Cargo\.toml|go\.mod"
    r"|Gemfile|composer\.json)$"
)
# Calls that reach the network, in code a PR adds. Docs that show a curl command don't count.
NETWORK_RE = re.compile(
    r"\bfetch\s*\(|\bhttps?\.(?:request|get)\s*\(|\baxios\b|\bnet\.(?:connect|createConnection)\s*\(|\bdgram\b"
    r"|\bWebSocket\s*\(|\brequests\.(?:get|post|put|patch|delete|request)\s*\(|\burllib\.request\b|\bhttpx\b"
    r"|\bsocket\.(?:socket|create_connection)\s*\(|\b(?:curl|wget)\s"
)
# Reading a credential by name, dumping the whole environment, or opening a credentials file.
CREDENTIAL_RE = re.compile(
    r"(?:process\.env|os\.environ)(?:\.get\(\s*['\"]|\.|\[\s*['\"])\w*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY|CREDENTIAL)"
    r"|(?:Object\.(?:keys|entries|values)|JSON\.stringify)\(\s*process\.env\s*\)|dict\(\s*os\.environ\s*\)"
    r"|\.ssh/|\.npmrc|\.aws/credentials|\.git-credentials|\.netrc|/proc/self/environ"
)
DOC_SUFFIXES = frozenset({".md", ".mdx", ".rst", ".txt", ".adoc"})
# What a failed connection prints inside the test phase, where there is no network.
BLOCKED_NETWORK_RE = re.compile(
    r"EAI_AGAIN|ENETUNREACH|ENOTFOUND|Temporary failure in name resolution|Network is unreachable"
    r"|Could not resolve host"
)
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


def _added_code(diff_text: str) -> list[str]:
    """Lines a diff adds to files that aren't docs."""
    lines, path, in_header = [], "", False
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            in_header = True
        elif in_header and line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("@@"):
            in_header = False
        elif not in_header and line.startswith("+") and path and PurePosixPath(path).suffix not in DOC_SUFFIXES:
            lines.append(line[1:])
    return lines


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
    # Dependencies are installed with network access, so the maintainer should know.
    if any(MANIFEST_RE.search(f) for f in files):
        flags.append("dependencies changed")
    code = "\n".join(_added_code(diff_text))
    if NETWORK_RE.search(code):
        flags.append("network call added")
    if CREDENTIAL_RE.search(code):
        flags.append("reads credentials")
    return tuple(flags)


def runtime_flags(output: str) -> tuple[str, ...]:
    """Flags from what a PR's tests printed in the test phase."""
    return ("blocked network attempt",) if BLOCKED_NETWORK_RE.search(output) else ()


def _docker_argv(scratch: Path, image: str, cmd: str, name: str, extra: list[str]) -> list[str]:
    # --memory-swap equal to --memory: 1g means 1g, not 1g plus the VM's swap.
    return [
        "docker", "run", *extra,
        "--memory", "1g", "--memory-swap", "1g", "--pids-limit", "256",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--name", name, "-v", f"{scratch}:/work", "-w", "/work",
        image, "sh", "-c", cmd,
    ]


def docker_install_argv(scratch: Path, image: str, cmd: str, name: str) -> list[str]:
    """Installing dependencies may download them, so this phase keeps the network.

    No --rm: `run_tests` commits the stopped container as the test phase's image,
    so packages installed outside /work are still there, then removes it.
    """
    return _docker_argv(scratch, image, cmd, name, [])


def docker_test_argv(scratch: Path, image: str, cmd: str, name: str) -> list[str]:
    return _docker_argv(scratch, image, cmd, name, ["--rm", "--network", "none"])


def _tail(text: str) -> str:
    return "\n".join(text.splitlines()[-TAIL_LINES:])


def _contained(argv: list[str], name: str, timeout: int) -> tuple[int | None, str]:
    """Run one container, keeping only the last OUTPUT_CAP bytes of its output.

    On timeout, remove the container and return None as the exit code.
    """
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    kept = bytearray()

    def read() -> None:
        while chunk := proc.stdout.read1(65536):
            kept.extend(chunk)
            del kept[:-OUTPUT_CAP]

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code: int | None = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        # Killing the docker CLI alone would leave the container running.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        proc.kill()
        proc.wait()
        code = None
    reader.join(timeout=5)
    return code, kept.decode(errors="replace")


def run_tests(
    worktree: Path, image: str, install_cmd: str | None, test_cmd: str, timeout: int = 300
) -> tuple[TestStatus, str, tuple[str, ...]]:
    """Run `test_cmd` against a scratch copy of `worktree`.

    Returns the status, the output's last lines, and `runtime_flags` from all of the
    test phase's output (an attempt can print anywhere, not just at the end).
    """
    scratch = Path(tempfile.mkdtemp(prefix="issue-desk-"))
    name = f"issue-desk-{uuid.uuid4().hex[:12]}"
    installed: str | None = None
    try:
        work = scratch / "work"
        shutil.copytree(worktree, work, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        if install_cmd:
            try:
                code, out = _contained(docker_install_argv(work, image, install_cmd, name), name, timeout)
                if code == 0:
                    installed = f"{name}:installed"
                    commit = subprocess.run(["docker", "commit", name, installed], capture_output=True, text=True)
                    if commit.returncode != 0:
                        installed = None
                        return "error", _tail(out + commit.stderr), ()
            finally:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            # The install phase has the network, so its output says nothing about attempts.
            if code is None or code == KILLED:
                return "timeout", _tail(out), ()
            if code != 0:
                return "error", _tail(out), ()
        code, out = _contained(docker_test_argv(work, installed or image, test_cmd, name), name, timeout)
        flags = runtime_flags(out)
        if code is None or code == KILLED:
            return "timeout", _tail(out), flags
        if code in DOCKER_ERRORS:
            return "error", _tail(out), flags
        return ("pass" if code == 0 else "fail"), _tail(out), flags
    finally:
        if installed:
            subprocess.run(["docker", "rmi", "-f", installed], capture_output=True)
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
