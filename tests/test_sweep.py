from triage_kit.models import IssueSummary, PullRequest
from triage_kit.sweep import HotIssue, closes_issues, hot_issues, render_sweep


def test_closes_issues_reads_closing_keywords():
    assert closes_issues("Closes #12, and fixes #3.") == {12, 3}
    assert closes_issues("resolved: #7") == {7}
    assert closes_issues("FIX #4") == {4}


def test_closes_issues_ignores_other_repos_and_glued_refs():
    assert closes_issues("fixes other/repo#12") == set()
    assert closes_issues("see #12abc") == set()
    assert closes_issues("fixes #12abc") == set()
    assert closes_issues("prefix #5") == set()


def test_closes_issues_ignores_plain_mentions():
    assert closes_issues("mentions #9") == set()


def pr(number: int, body: str) -> PullRequest:
    return PullRequest(number, f"PR {number}", body, "dev", f"b{number}", f"https://x/{number}")


def test_hot_issues_thresholds_and_order():
    issues = [
        IssueSummary(1, "quiet", 4, ()),
        IssueSummary(2, "busy thread", 5, ()),
        IssueSummary(3, "two prs", 0, ()),
    ]
    prs = [pr(10, "Fixes #1"), pr(11, "Closes #3"), pr(12, "resolves #3")]
    hot = hot_issues(issues, prs)
    assert [h.number for h in hot] == [3, 2]
    assert hot[0] == HotIssue(3, "two prs", 0, (11, 12))
    assert hot[1].pr_numbers == ()


def test_render_sweep_caps_rows_at_ten():
    hot = [HotIssue(n, f"Issue {n}", 20 - n, ()) for n in range(1, 13)]
    out = render_sweep(hot, "o/r")
    lines = out.splitlines()
    header = lines.index("| # | Title | Comments | Open PRs |")
    rows = [line for line in lines[header + 2:] if line.startswith("| ")]
    assert len(rows) == 10
    assert lines[-1] == "…and 2 more"


def test_render_sweep_escapes_pipes_and_lists_prs():
    out = render_sweep([HotIssue(5, "a | b", 7, (8, 9))], "o/r")
    assert "| #5 | a \\| b | 7 | 2 (#8, #9) |" in out


def test_render_sweep_with_nothing_hot():
    assert "No hot issues" in render_sweep([], "o/r")
