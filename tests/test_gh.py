import json
from pathlib import Path

from triage_kit.gh import fetch_issue, list_open_issue_summaries, list_open_prs
from triage_kit.models import Comment, PullRequest

FIXTURES = Path(__file__).parent / "fixtures"

ISSUE = {
    "number": 12,
    "title": "Add the thing",
    "body": "Please add the thing.",
    "user": {"login": "maint"},
    "author_association": "OWNER",
    "labels": [{"name": "hackathon"}],
}


class FakeGh:
    """Answers gh api calls from canned slurped pages and records every call."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        path = args[-1]
        for prefix, pages in self.routes.items():
            if path.startswith(prefix):
                return json.dumps(pages)
        raise AssertionError(f"unexpected path {path}")


def comment_pages() -> list[list[dict]]:
    return [json.loads((FIXTURES / f"issue_comments_p{n}.json").read_text()) for n in (1, 2)]


def test_fetch_issue_joins_all_comment_pages():
    gh = FakeGh({"repos/o/r/issues/12/comments": comment_pages(), "repos/o/r/issues/12": [ISSUE]})
    issue = fetch_issue("o/r", 12, run=gh)
    assert len(issue.comments) == 137
    assert issue.comments[0] == Comment("user0", "NONE", "Comment number 0", "2026-10-01T00:00:00Z")
    assert issue.comments[-1].body == "Comment number 136"
    assert (issue.number, issue.title, issue.author, issue.association) == (12, "Add the thing", "maint", "OWNER")
    assert issue.labels == ("hackathon",)


def test_issue_summaries_skip_pull_requests():
    items = [
        {"number": 1, "title": "a", "comments": 3, "labels": []},
        {"number": 2, "title": "b", "comments": 9, "labels": [], "pull_request": {"url": "x"}},
        {"number": 3, "title": "c", "comments": 0, "labels": [{"name": "bug"}]},
    ]
    gh = FakeGh({"repos/o/r/issues": [items[:2], items[2:]]})
    summaries = list_open_issue_summaries("o/r", None, run=gh)
    assert [s.number for s in summaries] == [1, 3]
    assert summaries[1].labels == ("bug",)
    assert summaries[0].comment_count == 3


def test_issue_summaries_filter_by_label():
    gh = FakeGh({"repos/o/r/issues": [[]]})
    list_open_issue_summaries("o/r", "good first issue", run=gh)
    assert "labels=good%20first%20issue" in gh.calls[0][-1]


def test_list_open_prs_reads_head_ref_and_url():
    pr = {"number": 7, "title": "Fix", "body": None, "user": {"login": "dev"},
          "head": {"ref": "fix-7"}, "html_url": "https://github.com/o/r/pull/7"}
    gh = FakeGh({"repos/o/r/pulls": [[pr]]})
    assert list_open_prs("o/r", run=gh) == [PullRequest(7, "Fix", "", "dev", "fix-7", "https://github.com/o/r/pull/7")]


def test_runner_args_use_paginate_slurp():
    gh = FakeGh({
        "repos/o/r/issues/12/comments": comment_pages(),
        "repos/o/r/issues/12": [ISSUE],
        "repos/o/r/issues": [[]],
        "repos/o/r/pulls": [[]],
    })
    fetch_issue("o/r", 12, run=gh)
    list_open_issue_summaries("o/r", "hackathon", run=gh)
    list_open_prs("o/r", run=gh)
    assert len(gh.calls) == 4
    for args in gh.calls:
        assert "--paginate" in args and "--slurp" in args
        assert "per_page=100" in args[-1] or args[-1] == "repos/o/r/issues/12"
