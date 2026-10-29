import re
import subprocess
from pathlib import Path

import pytest

from triage_kit.claims import (
    ClaimRow,
    anonymise,
    author_of,
    build_scorecard,
    check_refs,
    classify,
    is_maintainer,
    near_duplicates,
    referenced_refs,
    render_scorecard,
)
from triage_kit.models import Comment, Issue

BANNED = ("slop", "spam", "lazy", "ai-generated")

PLAN = (
    "I'd like to take this. Plan: add a `load_plugins()` helper in core/x/loader.py that reads "
    "the manifest, validates each entry against the schema, and registers the plugin with the "
    "registry. Then I'll add tests in tests/test_loader.py covering a missing manifest, a bad "
    "schema and a duplicate plugin name, and update the docs page that lists the plugin hooks "
    "so the new option is described. Should take two days."
)
assert len(PLAN) >= 280


def issue_with(comments: list[Comment], author: str = "petrov", body: str = "Add the loader.") -> Issue:
    return Issue(1, "Add plugin loader", body, author, "OWNER", tuple(comments), ())


# --- authors and the mirror header --------------------------------------------------------


def test_header_beats_owner_association():
    c = Comment("JemIIahh", "OWNER", "**contributor-2** wrote:\n\ncan I take this?", "")
    assert author_of(c) == "contributor-2"
    assert is_maintainer(c) is False


def test_maintainer_header_counts_as_maintainer():
    c = Comment("JemIIahh", "OWNER", "**maintainer** wrote:\n\nPlease use the registry.", "")
    assert author_of(c) == "maintainer"
    assert is_maintainer(c) is True


def test_without_header_association_decides():
    assert is_maintainer(Comment("petrov", "MEMBER", "go ahead", "")) is True
    assert is_maintainer(Comment("alice", "NONE", "can I?", "")) is False
    assert author_of(Comment("alice", "NONE", "can I?", "")) == "alice"


def test_anonymise_replaces_mentions():
    issue = issue_with(
        [
            Comment("alice", "NONE", "can I take this? cc @petrov", ""),
            Comment("bobby", "CONTRIBUTOR", "> @alice can I take this?\n\nI'd like it too, @Carol knows the code. "
                    "Use `@pytest.fixture` here.", ""),
            Comment("petrov", "MEMBER", "@bobby go ahead", ""),
            Comment("alice", "NONE", "ok", ""),
        ],
        body="Ping @alice or @carol.",
    )
    out = anonymise(issue)
    text = "\n".join([out.title, out.body, out.author] + [f"{c.author}\n{c.body}" for c in out.comments])
    for login in ("alice", "bobby", "petrov", "carol"):
        assert login not in text.lower()
    assert [c.author for c in out.comments] == ["contributor-1", "contributor-2", "maintainer", "contributor-1"]
    assert out.author == "maintainer"
    assert "> @contributor-1 can I take this?" in out.comments[1].body
    assert "`@pytest.fixture`" in out.comments[1].body
    assert out.comments[2].body == "@contributor-2 go ahead"


# --- classify -----------------------------------------------------------------------------


def test_classify_bare_requests():
    assert classify("can I take this?", False) == "bare"
    assert classify("+1", False) == "bare"


def test_classify_plan_naming_a_file():
    assert classify(PLAN, False) == "plan"


def test_classify_short_comment_with_a_ref_is_a_plan():
    assert classify("I'd fix it in `core/x/loader.py`.", False) == "plan"


def test_classify_repeat_author_is_follow_up():
    assert classify(PLAN, True) == "follow-up"
    assert classify("any update?", True) == "follow-up"


# --- near-duplicates ----------------------------------------------------------------------


def test_near_duplicates_maps_later_to_earlier():
    edited = PLAN.replace("Should take two days.", "Should take 3 days.").replace("I'd like", "I would like")
    other = "Totally different idea: rewrite the registry in Rust and expose it over gRPC to every client."
    dups = near_duplicates([PLAN, edited, other])
    assert set(dups) == {1}
    index, ratio = dups[1]
    assert index == 0 and ratio >= 0.80


def test_near_duplicates_ignore_quotes_and_mentions():
    a = "> @someone said hi\n\nI will add a loader in core/x/loader.py with tests and docs for every hook."
    b = "@petrov I will add a loader in core/x/loader.py with tests and docs for every hook."
    assert near_duplicates([a, b])[1][0] == 0


# --- refs ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        "see `core/x/loader.py`",
        "the bug is at core/x/loader.py:123 I think",
        "edit ./core/x/loader.py please",
        "https://github.com/o/r/blob/main/core/x/loader.py#L5",
    ],
)
def test_referenced_refs_normalises_paths(body):
    assert "core/x/loader.py" in referenced_refs(body)


def test_referenced_refs_identifiers_and_bare_filenames():
    refs = referenced_refs("Call `run()` and `Registry.add(name)` from loader.py, see e.g. v0.40.1 on github.com.")
    assert {"run", "add", "loader.py"} <= refs
    assert not {"e.g", "v0.40.1", "github.com", "0.40.1"} & refs


def test_referenced_refs_skips_other_urls():
    assert referenced_refs("docs at https://example.com/guide/setup.html") == set()


def git_repo(tmp_path: Path) -> Path:
    files = {
        "core/x/loader.py": "def run():\n    return 1\n",
        "src/deep/util.py": "VALUE = 2\n",
        "catalog/github/connection.yaml": "name: github\n",
    }
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    return tmp_path


def test_check_refs_against_a_real_checkout(tmp_path):
    repo = git_repo(tmp_path)
    result = check_refs({"core/x/loader.py", "core/x/missing.py", "util.py", "nothere.py", "run", "fly_away",
                         "catalog/github/", "../outside.py"}, repo)
    assert result == {
        "core/x/loader.py": True,
        "core/x/missing.py": False,
        "util.py": True,
        "nothere.py": False,
        "run": True,
        "fly_away": False,
        "catalog/github/": True,
        "../outside.py": False,
    }


# --- scorecard ----------------------------------------------------------------------------


def test_build_scorecard_counts_and_notes(tmp_path):
    repo = git_repo(tmp_path)
    edited = PLAN.replace("Should take two days.", "Should take 3 days.")
    issue = issue_with([
        Comment("alice", "NONE", "can I take this?", ""),
        Comment("bobby", "NONE", PLAN, ""),
        Comment("petrov", "MEMBER", "Please keep it inside the registry. Don't add a new module.", ""),
        Comment("carol", "NONE", edited, ""),
        Comment("alice", "NONE", "any update?", ""),
    ])
    rows, notes = build_scorecard(issue, repo)
    assert [(r.index, r.kind) for r in rows] == [(1, "bare"), (2, "plan"), (4, "near-duplicate"), (5, "follow-up")]
    assert rows[2].duplicate_of == 2 and rows[2].ratio >= 0.8
    assert rows[1].refs["core/x/loader.py"] is True
    assert rows[1].refs["tests/test_loader.py"] is False
    assert rows[1].refs["load_plugins"] is False
    assert notes == ["Please keep it inside the registry. Don't add a new module."]
    assert len(rows[1].summary) <= 100


def row(index: int, kind: str, **kw) -> ClaimRow:
    base = dict(index=index, author=f"contributor-{index}", kind=kind, duplicate_of=None, ratio=None,
                refs={}, summary=f"Summary {index}.")
    return ClaimRow(**(base | kw))


def test_render_scorecard_summary_line_and_cap():
    rows = [row(i, "bare") for i in range(1, 12)] + [
        row(12, "plan", refs={"core/x/loader.py": True, "gone.py": False}),
        row(13, "near-duplicate", duplicate_of=12, ratio=0.92),
        row(14, "follow-up"),
    ]
    out = render_scorecard(rows, [])
    assert out.splitlines()[0] == "14 claims → 11 bare, 1 plans, 1 near-duplicates, 1 follow-ups"
    assert len([line for line in out.splitlines() if re.match(r"\| \d+ \|", line)]) == 10
    assert "…and 4 more" in out
    assert "none yet" in out
    full = render_scorecard(rows, ["Use the registry."], max_rows=None)
    assert "`core/x/loader.py` ✓" in full and "`gone.py` ✗" in full
    assert "92% like #12" in full
    assert "Maintainer direction so far" in full and "Use the registry." in full


def test_render_has_no_verdict_words():
    rows = [row(1, "bare", summary="This is spam and lazy AI-generated slop.")]
    out = render_scorecard(rows, ["No SPAM please."]).lower()
    for word in BANNED:
        assert word not in out


def test_check_refs_accepts_trailing_partial_paths(tmp_path):
    repo = git_repo(tmp_path)
    assert check_refs({"x/loader.py", "github/", "x/util.py", "ore/x/loader.py"}, repo) == {
        "x/loader.py": True,
        "github/": True,
        "x/util.py": False,
        "ore/x/loader.py": False,
    }


def test_summary_skips_greetings_list_markers_and_code(tmp_path):
    repo = git_repo(tmp_path)
    issue = issue_with([
        Comment("alice", "NONE", "Hi @petrov @bobby 👋, I'd like to take this one if it's free.", ""),
        Comment("bobby", "NONE", "1. Add a loader in core/x/loader.py with tests.\n2. Update docs.", ""),
        Comment("carol", "NONE", "```mermaid\nflowchart TD A --> B\n```\nThe loader should live in the registry module.", ""),
    ])
    rows, _ = build_scorecard(issue, repo)
    assert [r.summary for r in rows] == [
        "I'd like to take this one if it's free.",
        "Add a loader in core/x/loader.py with tests.",
        "The loader should live in the registry module.",
    ]
