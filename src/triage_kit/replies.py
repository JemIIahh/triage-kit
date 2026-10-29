"""Thank the people who weren't picked, pointing each to an open issue nobody is assigned to yet.

Nothing here posts: the maintainer reads the drafts and posts them. They go out as a few
comments, not one per person, because everyone who commented is subscribed to the thread
and fifty separate replies would send each of them fifty notifications.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from triage_kit.claims import author_of, claim_text, classify, is_maintainer
from triage_kit.markdown import cell, plain
from triage_kit.mirror import inert
from triage_kit.models import Issue, IssueSummary, PullRequest
from triage_kit.sweep import closes_issues

# GitHub reportedly stops notifying past about 50 mentions in one comment; half that is safe.
MAX_MENTIONS = 25
MAX_ROWS = 10

INTRO = "Thank you for offering to help with this issue. It has gone to someone else this time."
POINTERS = "Here's an open issue for each of you that isn't assigned to anyone yet, in case you'd like to try for it:"
PLAN_THANKS = " (thank you for the detailed plan)"
NOTHING_LEFT = ": there isn't another unassigned issue to point you to right now, sorry."


@dataclass(frozen=True)
class Reply:
    login: str
    plan: bool
    suggestion: IssueSummary | None
    # A pseudonym from a mirrored copy: never a live mention.
    mirrored: bool


def free_issues(issues: Iterable[IssueSummary], prs: Iterable[PullRequest], exclude: int) -> list[IssueSummary]:
    """Open issues with no assignee and no PR closing them, least crowded first."""
    taken = {n for p in prs for n in closes_issues(p.body)}
    free = [i for i in issues if i.number != exclude and not i.assignees and i.number not in taken]
    return sorted(free, key=lambda i: (i.comment_count, i.number))


def draft_replies(
    issue: Issue, picked: Iterable[str], free: Sequence[IssueSummary], per_issue: int = 1
) -> list[Reply]:
    """One reply per claimant who wasn't picked, in thread order.

    People who wrote a plan get first pick of the free issues, and each issue goes to at
    most `per_issue` people, so the replies don't start a new flood somewhere else.
    """
    skip = {p.lower() for p in picked} | {"ghost"}
    logins: dict[str, str] = {}
    plans: dict[str, bool] = {}
    mirrored: dict[str, bool] = {}
    for c in issue.comments:
        login = author_of(c)
        key = login.lower()
        if is_maintainer(c) or key in skip or key.endswith("[bot]"):
            continue
        logins.setdefault(key, login)
        plans[key] = plans.get(key, False) or classify(claim_text(c), False) == "plan"
        mirrored[key] = login != c.author

    slots = iter([f for _ in range(per_issue) for f in free])
    suggestion = {k: next(slots, None) for k in sorted(logins, key=lambda k: not plans[k])}
    return [Reply(logins[k], plans[k], suggestion[k], mirrored[k]) for k in logins]


def _comment(replies: list[Reply]) -> str:
    parts = [INTRO]
    pointed = [r for r in replies if r.suggestion]
    if pointed:
        bullets = [
            f"- @{r.login}{PLAN_THANKS if r.plan else ''}: #{r.suggestion.number} {plain(r.suggestion.title)}"
            for r in pointed
        ]
        parts.append(POINTERS + "\n\n" + "\n".join(bullets))
    rest = [r for r in replies if not r.suggestion]
    if rest:
        parts.append(", ".join(f"@{r.login}" for r in rest) + NOTHING_LEFT)
    text = "\n\n".join(parts)
    return inert(text) if any(r.mirrored for r in replies) else text


def reply_comments(replies: list[Reply]) -> list[str]:
    """The GitHub comments to post, at most MAX_MENTIONS people each."""
    return [_comment(replies[i : i + MAX_MENTIONS]) for i in range(0, len(replies), MAX_MENTIONS)]


def render_replies(replies: list[Reply], max_rows: int | None = MAX_ROWS) -> str:
    """The batch for the maintainer to approve, as markdown for chat."""
    if not replies:
        return "Nobody else to reply to."
    n_pointed = sum(1 for r in replies if r.suggestion)
    n_comments = len(reply_comments(replies))
    who = "1 person wasn't" if len(replies) == 1 else f"{len(replies)} people weren't"
    lines = [
        f"**{who} picked** → {n_pointed} pointed to an unassigned issue, {len(replies) - n_pointed} thanked only. "
        f"Goes out as {n_comments} comment{'' if n_comments == 1 else 's'}.",
        "",
        "| Who | Kind | Points to |",
        "|---|---|---|",
    ]
    shown = replies if max_rows is None else replies[:max_rows]
    for r in shown:
        points = f"#{r.suggestion.number} {cell(r.suggestion.title)}" if r.suggestion else "—"
        lines.append(f"| {cell(r.login)} | {'plan' if r.plan else 'bare'} | {points} |")
    if len(shown) < len(replies):
        lines.append(f"…and {len(replies) - len(shown)} more")
    return "\n".join(lines)
