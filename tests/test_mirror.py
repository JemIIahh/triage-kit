import json
import re

from triage_kit.claims import MIRROR_HEADER_RE, author_of, is_maintainer
from triage_kit.mirror import mirror_body, mirror_comment, mirror_issue
from triage_kit.models import Comment, Issue

SOURCE_ISSUE = {
    "number": 12,
    "title": "Add the loader (ping @petrov)",
    "body": "We need a loader. cc @alice",
    "user": {"login": "petrov"},
    "author_association": "OWNER",
    "labels": [],
}
SOURCE_COMMENTS = [
    {"user": {"login": "alice"}, "author_association": "NONE", "body": "can I take this @petrov?", "created_at": "t1"},
    {"user": {"login": "bobby"}, "author_association": "NONE", "body": "> @alice can I\n\nme too", "created_at": "t2"},
    {"user": {"login": "petrov"}, "author_association": "MEMBER", "body": "@bobby go ahead", "created_at": "t3"},
    {"user": {"login": "bobby"}, "author_association": "NONE",
     "body": "Done in https://github.com/bobby/repo/pull/9, see #5 and other/repo#7. Thanks `@alice` @petrov",
     "created_at": "t4"},
]
LOGINS = ("petrov", "alice", "bobby")


class RecordingGh:
    def __init__(self):
        self.posts: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        if "POST" in args:
            self.posts.append(args)
            return json.dumps({"number": 5, "id": len(self.posts)})
        path = args[-1]
        if path.startswith("repos/src/repo/issues/12/comments"):
            return json.dumps([SOURCE_COMMENTS])
        if path == "repos/src/repo/issues/12":
            return json.dumps([SOURCE_ISSUE])
        raise AssertionError(f"unexpected call {args}")


def test_mirror_round_trip():
    for pseudonym, maintainer in (("contributor-2", False), ("maintainer", True)):
        c = Comment(pseudonym, "NONE", "hello", "")
        posted = Comment("JemIIahh", "OWNER", mirror_comment(c), "")
        assert author_of(posted) == pseudonym
        assert is_maintainer(posted) is maintainer


def test_mirror_body_footer_does_not_link_back():
    issue = Issue(12, "t", "Body text.", "maintainer", "OWNER", (), ())
    body = mirror_body(issue, "src/repo")
    assert body.startswith("Body text.")
    assert "`src/repo#12`" in body
    assert "Usernames replaced." in body


def test_mirror_posts_no_real_logins():
    gh = RecordingGh()
    assert mirror_issue("src/repo", 12, "me/sandbox", run=gh) == 5
    assert gh.posts[0][gh.posts[0].index("POST") + 1] == "repos/me/sandbox/issues"
    assert all(p[p.index("POST") + 1] == "repos/me/sandbox/issues/5/comments" for p in gh.posts[1:])
    assert len(gh.posts) == 1 + len(SOURCE_COMMENTS)
    posted = "\n".join(arg for p in gh.posts for arg in p).lower()
    for login in LOGINS:
        assert login not in posted
    for p in gh.posts[1:]:
        body = next(a for a in p if a.startswith("body="))[len("body="):]
        assert MIRROR_HEADER_RE.match(body)


def posted_bodies(gh: RecordingGh) -> list[str]:
    return [next(a for a in p if a.startswith("body="))[len("body="):] for p in gh.posts]


def test_mirror_makes_mentions_and_references_inert():
    gh = RecordingGh()
    mirror_issue("src/repo", 12, "me/sandbox", run=gh)
    bodies = posted_bodies(gh)
    for body in bodies:
        prose = re.sub(r"`[^`]*`", "", body)
        assert not re.search(r"@\w", prose), body
        assert not re.search(r"#\d", prose), body
        assert "https://github.com/" not in prose, body
    last = bodies[-1]
    assert "`https://github.com/contributor-2/repo/pull/9`" in last
    assert "`#5`" in last and "`other/repo#7`" in last
    assert "＠maintainer" in last
