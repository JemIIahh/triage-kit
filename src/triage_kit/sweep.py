"""Find the issues drawing the most claims and competing pull requests."""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from triage_kit.markdown import cell
from triage_kit.models import IssueSummary, PullRequest

# A closing keyword, an optional colon, whitespace, then #N for this repo only:
# `other/repo#12` has no whitespace before the `#`, and `#12abc` fails the \b.
CLOSING_RE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\s+#(\d+)\b", re.IGNORECASE)

MAX_ROWS = 10


@dataclass(frozen=True)
class HotIssue:
    number: int
    title: str
    comment_count: int
    pr_numbers: tuple[int, ...]


def closes_issues(body: str) -> set[int]:
    return {int(n) for n in CLOSING_RE.findall(body)}


def hot_issues(
    issues: Iterable[IssueSummary], prs: Iterable[PullRequest], min_comments: int = 5, min_prs: int = 2
) -> list[HotIssue]:
    closing: dict[int, list[int]] = {}
    for p in prs:
        for n in closes_issues(p.body):
            closing.setdefault(n, []).append(p.number)
    hot = [
        HotIssue(i.number, i.title, i.comment_count, tuple(sorted(closing.get(i.number, []))))
        for i in issues
        if i.comment_count >= min_comments or len(closing.get(i.number, [])) >= min_prs
    ]
    return sorted(hot, key=lambda h: (-len(h.pr_numbers), -h.comment_count, h.number))


def render_sweep(hot: list[HotIssue], repo: str) -> str:
    if not hot:
        return f"No hot issues in {repo}."
    lines = [
        f"**{len(hot)} hot issues in {repo}**",
        "",
        "| # | Title | Comments | Open PRs |",
        "|---|---|---|---|",
    ]
    for h in hot[:MAX_ROWS]:
        prs = f"{len(h.pr_numbers)} ({', '.join(f'#{n}' for n in h.pr_numbers)})" if h.pr_numbers else "0"
        lines.append(f"| #{h.number} | {cell(h.title)} | {h.comment_count} | {prs} |")
    if len(hot) > MAX_ROWS:
        lines.append(f"…and {len(hot) - MAX_ROWS} more")
    return "\n".join(lines)
