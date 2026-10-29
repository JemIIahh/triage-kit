"""Read GitHub through the local `gh` login. Every read is paginated in full."""

import json
import subprocess
from collections.abc import Callable
from urllib.parse import quote

from triage_kit.models import Comment, Issue, IssueSummary, PullRequest

Runner = Callable[[list[str]], str]


class GhError(RuntimeError):
    pass


class GitError(RuntimeError):
    pass


def run_gh(args: list[str]) -> str:
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(proc.stderr.strip())
    return proc.stdout


def run_git(args: list[str]) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip())
    return proc.stdout


def _pages(path: str, run: Runner) -> list:
    """Every page of `path`, as `gh api --paginate --slurp` returns them."""
    return json.loads(run(["api", "--paginate", "--slurp", path]))


def _items(path: str, run: Runner) -> list[dict]:
    return [item for page in _pages(path, run) for item in page]


def _login(obj: dict) -> str:
    # Deleted accounts come back as a null user.
    return (obj.get("user") or {}).get("login") or "ghost"


def fetch_issue(repo: str, number: int, run: Runner = run_gh) -> Issue:
    raw = _pages(f"repos/{repo}/issues/{number}", run)[0]
    comments = tuple(
        Comment(_login(c), c.get("author_association", "NONE"), c.get("body") or "", c.get("created_at", ""))
        for c in _items(f"repos/{repo}/issues/{number}/comments?per_page=100", run)
    )
    return Issue(
        number=raw["number"],
        title=raw["title"],
        body=raw.get("body") or "",
        author=_login(raw),
        association=raw.get("author_association", "NONE"),
        comments=comments,
        labels=tuple(label["name"] for label in raw.get("labels", [])),
    )


def list_open_issue_summaries(repo: str, label: str | None, run: Runner = run_gh) -> list[IssueSummary]:
    path = f"repos/{repo}/issues?state=open&per_page=100"
    if label:
        path += f"&labels={quote(label)}"
    return [
        IssueSummary(
            i["number"],
            i["title"],
            i.get("comments", 0),
            tuple(lb["name"] for lb in i.get("labels", [])),
            tuple(a["login"] for a in i.get("assignees") or []),
        )
        for i in _items(path, run)
        if "pull_request" not in i
    ]


def list_open_prs(repo: str, run: Runner = run_gh) -> list[PullRequest]:
    return [
        PullRequest(p["number"], p["title"], p.get("body") or "", _login(p), p["head"]["ref"], p["html_url"])
        for p in _items(f"repos/{repo}/pulls?state=open&per_page=100", run)
    ]
