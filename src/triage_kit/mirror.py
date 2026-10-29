"""Copy an issue into a sandbox repo with usernames replaced, for rehearsals.

This is one of only two things in triage-kit that write to GitHub, and it writes
only to `dst_repo`.
"""

import json
import re

from triage_kit.claims import CODE_RE, MENTION_RE, anonymise
from triage_kit.gh import Runner, fetch_issue, run_gh
from triage_kit.models import Comment, Issue

GITHUB_URL_RE = re.compile(r"https?://github\.com/[^\s)\]>`]*[^\s)\]>`.,;:!?]")
ISSUE_REF_RE = re.compile(r"(?<![\w`/#&])((?:[\w.-]+/[\w.-]+)?#\d+)\b")


def inert(text: str) -> str:
    """Text that notifies nobody and cross-references nothing when GitHub renders it.

    Mentions get a full-width `＠`; issue refs and GitHub URLs go into code spans,
    which GitHub never links. Code the author wrote is left alone.
    """
    parts = CODE_RE.split(text)
    for i in range(0, len(parts), 2):
        part = MENTION_RE.sub(lambda m: "＠" + m.group(1), parts[i])
        part = ISSUE_REF_RE.sub(r"`\1`", part)
        parts[i] = GITHUB_URL_RE.sub(lambda m: f"`{m.group(0)}`", part)
    return "".join(parts)


def mirror_comment(c: Comment) -> str:
    return f"**{c.author}** wrote:\n\n{inert(c.body)}"


def mirror_body(issue: Issue, src_repo: str) -> str:
    # In backticks so GitHub doesn't add a cross-reference to the source issue.
    return f"{inert(issue.body)}\n\n---\nMirrored from `{src_repo}#{issue.number}` for a rehearsal. Usernames replaced."


def mirror_issue(src_repo: str, number: int, dst_repo: str, run: Runner = run_gh) -> int:
    """Post an anonymised copy of the issue and its comments; return the new issue number."""
    issue = anonymise(fetch_issue(src_repo, number, run))
    created = json.loads(run([
        "api", "-X", "POST", f"repos/{dst_repo}/issues",
        "-f", f"title={inert(issue.title)}", "-f", f"body={mirror_body(issue, src_repo)}",
    ]))
    new_number = created["number"]
    for c in issue.comments:
        run(["api", "-X", "POST", f"repos/{dst_repo}/issues/{new_number}/comments", "-f", f"body={mirror_comment(c)}"])
    return new_number
