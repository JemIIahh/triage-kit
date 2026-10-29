"""Copy an issue into a sandbox repo with usernames replaced, for rehearsals.

This is one of only two things in triage-kit that write to GitHub, and it writes
only to `dst_repo`.
"""

import json

from triage_kit.claims import anonymise
from triage_kit.gh import Runner, fetch_issue, run_gh
from triage_kit.models import Comment, Issue


def mirror_comment(c: Comment) -> str:
    return f"**{c.author}** wrote:\n\n{c.body}"


def mirror_body(issue: Issue, src_repo: str) -> str:
    # In backticks so GitHub doesn't add a cross-reference to the source issue.
    return f"{issue.body}\n\n---\nMirrored from `{src_repo}#{issue.number}` for a rehearsal. Usernames replaced."


def mirror_issue(src_repo: str, number: int, dst_repo: str, run: Runner = run_gh) -> int:
    """Post an anonymised copy of the issue and its comments; return the new issue number."""
    issue = anonymise(fetch_issue(src_repo, number, run))
    created = json.loads(run([
        "api", "-X", "POST", f"repos/{dst_repo}/issues",
        "-f", f"title={issue.title}", "-f", f"body={mirror_body(issue, src_repo)}",
    ]))
    new_number = created["number"]
    for c in issue.comments:
        run(["api", "-X", "POST", f"repos/{dst_repo}/issues/{new_number}/comments", "-f", f"body={mirror_comment(c)}"])
    return new_number
