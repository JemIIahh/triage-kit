"""Sort the claim comments on an issue and check what they say against the code."""

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath
from typing import Literal

from triage_kit.gh import GitError, run_git
from triage_kit.markdown import cell, mask_verdicts, plain
from triage_kit.models import MAINTAINER_ASSOCIATIONS, Comment, Issue

# Mirrored comments are all posted by the sandbox owner, so on an OWNER comment this
# header, not the GitHub author or association, says who wrote them.
MIRROR_HEADER_RE = re.compile(r"^\*\*(contributor-\d+|maintainer)\*\* wrote:\n\n")

# `＠` is the inert form mirrored copies use, so a mirrored mention still reads as one.
MENTION_RE = re.compile(r"(?<![\w@＠/.-])[@＠]([A-Za-z0-9][A-Za-z0-9-]{0,38})(?![\w-])")
PROFILE_RE = re.compile(r"(github\.com/)([A-Za-z0-9][A-Za-z0-9-]{0,38})(?![\w-])", re.IGNORECASE)
CODE_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
FENCE_RE = re.compile(r"```.*?(?:```|$)", re.DOTALL)
GREETING_RE = re.compile(r"^(?:hi|hello|hey|gm|good (?:morning|afternoon|evening))\b[^\w@]*(?:@[\w-]+[^\w@]*)*",
                         re.IGNORECASE)

# Drips Wave posts each application under the applicant's account, wrapping their own
# words in a fixed template; only the words between these markers are theirs.
WAVE_TEXT_RE = re.compile(r"<!-- wave:app-text-start -->\n(.*?)\n?<!-- wave:app-text-end -->", re.DOTALL)

BARE_MAX_CHARS = 280
NOTE_MAX_CHARS = 280
SUMMARY_MAX_CHARS = 100

SOURCE_EXTS = frozenset(
    "py pyi js mjs cjs ts tsx jsx go rs rb java kt swift c h cc cpp hpp cs php vue svelte "
    "md rst txt yaml yml json toml ini cfg sh bash sql html css scss proto lock".split()
)
# Product names that look like filenames in prose ("I know Node.js").
PRODUCT_NAMES = frozenset({"node.js", "next.js", "nuxt.js", "vue.js", "react.js", "express.js", "three.js",
                           "d3.js", "chart.js", "nest.js", "deno.js", "ember.js", "backbone.js"})
GITHUB_FILE_RE = re.compile(r"https?://github\.com/[\w.-]+/[\w.-]+/(?:blob|tree)/[^/\s]+/([\w./-]+)")
SAFE_REF_RE = re.compile(r"[\w./-]+")
URL_RE = re.compile(r"https?://\S+")
CALL_RE = re.compile(r"^(?:[A-Za-z_]\w*\.)*([A-Za-z_]\w*)\(.*\)$")
PATH_TOKEN_RE = re.compile(r"[\w./-]+(?::\d+)*(?:#L\d+(?:-L\d+)?)?")
LINE_SUFFIX_RE = re.compile(r"(?::\d+)+$|#L\d+(?:-L\d+)?$")

Kind = Literal["bare", "plan", "follow-up"]


@dataclass(frozen=True)
class ClaimRow:
    index: int
    author: str
    kind: str
    duplicate_of: int | None
    ratio: float | None
    refs: dict[str, bool]
    summary: str


# --- who wrote it -------------------------------------------------------------------------


def _mirrored_author(c: Comment) -> str | None:
    # Anyone can type the header; only the sandbox owner's comments carry it for real.
    m = MIRROR_HEADER_RE.match(c.body) if c.association == "OWNER" else None
    return m.group(1) if m else None


def author_of(c: Comment) -> str:
    return _mirrored_author(c) or c.author


def is_maintainer(c: Comment) -> bool:
    mirrored = _mirrored_author(c)
    if mirrored:
        return mirrored == "maintainer"
    return c.association in MAINTAINER_ASSOCIATIONS


def strip_header(c: Comment) -> str:
    return MIRROR_HEADER_RE.sub("", c.body, count=1) if _mirrored_author(c) else c.body


def claim_text(c: Comment) -> str:
    """What the commenter actually wrote: no mirror header, no application template."""
    body = strip_header(c)
    m = WAVE_TEXT_RE.search(body)
    if not m:
        return body
    return "\n".join(re.sub(r"^> ?", "", line) for line in m.group(1).splitlines())


def anonymise(issue: Issue) -> Issue:
    """Replace every login with `maintainer` or `contributor-N`, numbered by first appearance."""
    names: dict[str, str] = {}

    def name_for(login: str, association: str) -> str:
        key = login.lower()
        if key not in names:
            if association in MAINTAINER_ASSOCIATIONS:
                names[key] = "maintainer"
            else:
                names[key] = f"contributor-{sum(v != 'maintainer' for v in names.values()) + 1}"
        return names[key]

    author = name_for(issue.author, issue.association)
    authors = [name_for(c.author, c.association) for c in issue.comments]

    def pseudonym(m: re.Match, unknown: str | None) -> str:
        return "@" + names.get(m.group(1).lower(), unknown or m.group(1))

    def scrub(text: str) -> str:
        # Participants are replaced everywhere; other mentions only outside code,
        # where `@pytest.fixture` is code, not a person.
        parts = CODE_RE.split(text)
        for i, part in enumerate(parts):
            parts[i] = MENTION_RE.sub(lambda m: pseudonym(m, None if i % 2 else "someone"), part)
        return PROFILE_RE.sub(lambda m: m.group(1) + names.get(m.group(2).lower(), m.group(2)), "".join(parts))

    return replace(
        issue,
        title=scrub(issue.title),
        body=scrub(issue.body),
        author=author,
        comments=tuple(replace(c, author=a, body=scrub(c.body)) for c, a in zip(issue.comments, authors)),
    )


# --- what kind of claim -------------------------------------------------------------------


def _unquoted(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(">"))


def classify(body: str, repeat_author: bool) -> Kind:
    if repeat_author:
        return "follow-up"
    text = _unquoted(body).strip()
    if len(text) < BARE_MAX_CHARS and not referenced_refs(text):
        return "bare"
    return "plan"


def _normalise(text: str) -> str:
    return " ".join(MENTION_RE.sub("", _unquoted(text)).lower().split())


def near_duplicates(bodies: list[str], threshold: float = 0.80) -> dict[int, tuple[int, float]]:
    """Map each later body to its closest earlier body at or above `threshold`."""
    norm = [_normalise(b) for b in bodies]
    found: dict[int, tuple[int, float]] = {}
    for j in range(1, len(norm)):
        best: tuple[int, float] | None = None
        for i in range(j):
            # autojunk would treat common letters as noise in texts over 200 chars.
            sm = SequenceMatcher(None, norm[i], norm[j], autojunk=False)
            if sm.real_quick_ratio() < threshold or sm.quick_ratio() < threshold:
                continue
            ratio = sm.ratio()
            if ratio >= threshold and (best is None or ratio > best[1]):
                best = (i, ratio)
        if best:
            found[j] = best
    return found


# --- what it points at --------------------------------------------------------------------


def _clean_path(token: str) -> str:
    token = LINE_SUFFIX_RE.sub("", token.rstrip(".,;:")).rstrip(".,;:")
    while token.startswith("./"):
        token = token[2:]
    return token.lstrip("/")


def _is_filename(token: str) -> bool:
    name = PurePosixPath(token).name
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return ext in SOURCE_EXTS and name.lower() not in PRODUCT_NAMES


def referenced_refs(body: str) -> set[str]:
    """Paths and bare filenames a comment names, plus identifiers from backticked calls."""
    refs = {_clean_path(m.group(1)) for m in GITHUB_FILE_RE.finditer(body)}
    text = URL_RE.sub(" ", body)
    for m in CODE_RE.finditer(text):
        code = m.group(0).strip("`").strip()
        call = CALL_RE.match(code)
        if call:
            refs.add(call.group(1))
        else:
            path = _clean_path(code)
            if SAFE_REF_RE.fullmatch(path) and ("/" in path or _is_filename(path)):
                refs.add(path)
    for token in PATH_TOKEN_RE.findall(CODE_RE.sub(" ", text)):
        token = _clean_path(token)
        if token and _is_filename(token):
            refs.add(token)
    refs.discard("")
    return refs


def _git_grep(checkout: Path, name: str) -> bool:
    proc = subprocess.run(["git", "-C", str(checkout), "grep", "-q", "-w", "-F", "-e", name], capture_output=True)
    if proc.returncode > 1:
        raise GitError(proc.stderr.decode(errors="replace").strip())
    return proc.returncode == 0


def check_refs(refs: set[str], checkout: Path) -> dict[str, bool]:
    """A path must match whole trailing folders, a bare filename anywhere, an identifier as a whole word."""
    files = [f for f in run_git(["-C", str(checkout), "ls-files", "-z"]).split("\0") if f]
    file_set = set(files)
    names = {PurePosixPath(f).name for f in files}
    found: dict[str, bool] = {}
    for ref in refs:
        if "/" in ref:
            # People often name a file by its last few folders (`connections/loader.py`).
            path = ref.strip("/")
            found[ref] = ".." not in PurePosixPath(path).parts and (
                path in file_set
                or any(f.endswith("/" + path) or f.startswith(path + "/") or f"/{path}/" in f for f in files)
            )
        elif _is_filename(ref):
            found[ref] = ref in names
        else:
            found[ref] = _git_grep(checkout, ref)
    return found


# --- the scorecard ------------------------------------------------------------------------


def _one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def _summary(text: str) -> str:
    """The first real sentence: no greeting, list number or code block."""
    prose = FENCE_RE.sub("\n", _unquoted(text))
    sentences = [GREETING_RE.sub("", " ".join(s.split())) for s in re.split(r"(?<=[.!?])\s+|\n+", prose)]
    sentences = [s for s in sentences if s]
    first = next((s for s in sentences if len(s.split()) >= 4), sentences[0] if sentences else "")
    return _one_line(first, SUMMARY_MAX_CHARS)


def build_scorecard(issue: Issue, checkout: Path) -> tuple[list[ClaimRow], list[str]]:
    """One row per non-maintainer comment, plus the maintainer's comments as direction notes."""
    rows: list[ClaimRow] = []
    texts: list[str] = []
    notes: list[str] = []
    seen: set[str] = set()
    for index, c in enumerate(issue.comments, start=1):
        text = claim_text(c)
        if is_maintainer(c):
            notes.append(_one_line(text, NOTE_MAX_CHARS))
            continue
        who = author_of(c)
        kind = classify(text, who in seen)
        seen.add(who)
        rows.append(ClaimRow(index, who, kind, None, None, check_refs(referenced_refs(text), checkout), _summary(text)))
        texts.append(text)

    plans = [i for i, r in enumerate(rows) if r.kind == "plan"]
    for later, (earlier, ratio) in near_duplicates([texts[i] for i in plans]).items():
        row = rows[plans[later]]
        rows[plans[later]] = replace(row, kind="near-duplicate", duplicate_of=rows[plans[earlier]].index, ratio=ratio)
    return rows, notes


def _evidence(row: ClaimRow) -> str:
    parts = [f"{row.ratio:.0%} like #{row.duplicate_of}"] if row.kind == "near-duplicate" else []
    parts += [f"`{ref}` {'✓' if ok else '✗'}" for ref, ok in sorted(row.refs.items())]
    return ", ".join(parts) or "—"


def render_scorecard(rows: list[ClaimRow], notes: list[str], max_rows: int | None = 10) -> str:
    counts = Counter(r.kind for r in rows)
    lines = [
        f"{len(rows)} claims → {counts['bare']} bare, {counts['plan']} plans, "
        f"{counts['near-duplicate']} near-duplicates, {counts['follow-up']} follow-ups",
    ]
    if rows:
        lines += ["", "| # | Who | Kind | Evidence | Summary |", "|---|---|---|---|---|"]
        shown = rows if max_rows is None else rows[:max_rows]
        for r in shown:
            lines.append(f"| {r.index} | {r.author} | {r.kind} | {_evidence(r)} | {cell(mask_verdicts(r.summary))} |")
        if len(shown) < len(rows):
            lines.append(f"…and {len(rows) - len(shown)} more")
    lines += ["", "**Maintainer direction so far**"]
    lines += [f"- {plain(mask_verdicts(n))}" for n in notes] or ["none yet"]
    return "\n".join(lines)
