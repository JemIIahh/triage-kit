import re

from triage_kit.models import Comment, Issue, IssueSummary, PullRequest
from triage_kit.replies import MAX_MENTIONS, Reply, draft_replies, free_issues, render_replies, reply_comments

BANNED = ("slop", "spam", "lazy", "ai-generated")

PLAN = (
    "I'd like to take this. Plan: add a `createBackup()` helper in src/storage/history-backup.js that "
    "writes the run history atomically, then verify the checksum and add restore with tests in "
    "tests/history-backup.test.js covering a missing file, a bad checksum and a partial write, "
    "and document the new commands in the storage docs page so operators know how to use them."
)


def ask(author: str, body: str = "can I take this?", association: str = "NONE") -> Comment:
    return Comment(author, association, body, "")


def mirrored(n: int, body: str = "can I take this?") -> Comment:
    return Comment("JemIIahh", "OWNER", f"**contributor-{n}** wrote:\n\n{body}", "")


def issue_with(*comments: Comment) -> Issue:
    return Issue(183, "Backups for run history", "Add backups.", "petrov", "OWNER", comments, ())


def summary(n: int, comments: int = 0, assignees: tuple[str, ...] = (), title: str | None = None) -> IssueSummary:
    return IssueSummary(n, title or f"Issue {n}", comments, (), assignees)


def pointed(replies: list[Reply]) -> list[tuple[str, int | None]]:
    return [(r.login, r.suggestion.number if r.suggestion else None) for r in replies]


# --- which issues to point people to ------------------------------------------------------


def test_free_issues_are_unassigned_without_a_pr_least_crowded_first():
    issues = [
        summary(183, 55),
        summary(10, 4),
        summary(11, 2),
        summary(12, 0, assignees=("someone",)),
        summary(13, 1),
        summary(14, 2),
    ]
    prs = [PullRequest(5, "t", "Closes #13", "a", "h", "u")]
    assert [i.number for i in free_issues(issues, prs, exclude=183)] == [11, 14, 10]


# --- who gets a reply ---------------------------------------------------------------------


def test_one_reply_per_unpicked_claimant_in_thread_order():
    issue = issue_with(
        ask("alice"),
        ask("petrov", "Thanks all, give me a day to choose.", "MEMBER"),
        ask("bob"),
        ask("Alice", "any update?"),
        ask("Winner", PLAN),
        ask("dependabot[bot]", "Bumps lodash."),
        ask("ghost"),
        ask("carol"),
    )
    replies = draft_replies(issue, ["winner"], [])
    assert [r.login for r in replies] == ["alice", "bob", "carol"]


def test_plan_writers_get_free_issues_first():
    issue = issue_with(ask("alice"), ask("bob", PLAN), ask("carol"))
    replies = draft_replies(issue, [], [summary(11), summary(14)])
    assert pointed(replies) == [("alice", 14), ("bob", 11), ("carol", None)]
    assert [r.plan for r in replies] == [False, True, False]


def test_a_plan_in_a_follow_up_counts():
    issue = issue_with(ask("alice"), ask("bob"), ask("alice", PLAN))
    assert pointed(draft_replies(issue, [], [summary(11)])) == [("alice", 11), ("bob", None)]


def test_people_are_spread_across_free_issues():
    issue = issue_with(ask("a"), ask("b"), ask("c"), ask("d"), ask("e"))
    replies = draft_replies(issue, [], [summary(11), summary(14)], per_issue=2)
    assert pointed(replies) == [("a", 11), ("b", 14), ("c", 11), ("d", 14), ("e", None)]


# --- what they read -----------------------------------------------------------------------


def test_reply_comment_wording():
    replies = [
        Reply("alice", False, summary(14, title="Add retry to the uploader"), False),
        Reply("bob", True, summary(11, title="Fix the status page"), False),
        Reply("carol", False, None, False),
        Reply("dave", False, None, False),
    ]
    assert reply_comments(replies) == [
        "Thank you for offering to help with this issue. It has gone to someone else this time.\n\n"
        "Here's an open issue for each of you that isn't assigned to anyone yet, in case you'd like to try for it:\n\n"
        "- @alice: #14 Add retry to the uploader\n"
        "- @bob (thank you for the detailed plan): #11 Fix the status page\n\n"
        "@carol, @dave: there isn't another unassigned issue to point you to right now, sorry."
    ]


def test_reply_comment_with_nothing_to_point_to():
    assert reply_comments([Reply("carol", True, None, False)]) == [
        "Thank you for offering to help with this issue. It has gone to someone else this time.\n\n"
        "@carol: there isn't another unassigned issue to point you to right now, sorry."
    ]


def test_long_batches_split_so_nobody_misses_a_mention():
    replies = [Reply(f"person-{n}", False, summary(n) if n % 2 else None, False) for n in range(2 * MAX_MENTIONS + 1)]
    comments = reply_comments(replies)
    assert len(comments) == 3
    mentions = [re.findall(r"@(person-\d+)", c) for c in comments]
    assert all(len(m) <= MAX_MENTIONS for m in mentions)
    assert sorted(sum(mentions, [])) == sorted(r.login for r in replies)


def test_mirrored_claimants_are_never_pinged():
    issue = issue_with(mirrored(1), mirrored(2, PLAN), ask("JemIIahh", "**maintainer** wrote:\n\nThanks!", "OWNER"))
    replies = draft_replies(issue, [], [summary(11)])
    assert [(r.login, r.mirrored) for r in replies] == [("contributor-1", True), ("contributor-2", True)]
    body = reply_comments(replies)[0]
    assert "@" not in body and not re.search(r"#\d", body)
    assert "＠contributor-1" in body and "＠contributor-2 (thank you for the detailed plan): ＃11" in body


def test_titles_cannot_ping_or_link():
    title = "@scout please see [this](https://evil.example) | now"
    body = reply_comments([Reply("alice", False, summary(14, title=title), False)])[0]
    assert "@scout" not in body and "https://" not in body and "[this](" not in body


def test_no_verdict_words():
    replies = [Reply("a", True, summary(1), False), Reply("b", False, None, False)]
    text = " ".join(reply_comments(replies)).lower() + render_replies(replies).lower()
    assert not any(word in text for word in BANNED)


# --- the approval card --------------------------------------------------------------------


def test_render_replies():
    replies = [
        Reply("alice", False, summary(14, title="Add retry | fast"), False),
        Reply("bob", True, summary(11, title="Fix the status page"), False),
        Reply("carol", False, None, False),
    ]
    assert render_replies(replies).splitlines() == [
        "**3 people weren't picked** → 2 pointed to an unassigned issue, 1 thanked only. Goes out as 1 comment.",
        "",
        "| Who | Kind | Points to |",
        "|---|---|---|",
        "| alice | bare | #14 Add retry \\| fast |",
        "| bob | plan | #11 Fix the status page |",
        "| carol | bare | — |",
    ]


def test_render_replies_truncates_and_handles_nobody():
    replies = [Reply(f"p{n}", False, None, False) for n in range(12)]
    lines = render_replies(replies, max_rows=10).splitlines()
    assert lines[0].startswith("**12 people weren't picked** → 0 pointed")
    assert lines[-1] == "…and 2 more"
    assert render_replies([]) == "Nobody else to reply to."
