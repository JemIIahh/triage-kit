"""Small helpers for the markdown triage-kit posts into chat."""

import re

# Words the output never uses: it reports evidence, never a verdict on a person.
VERDICT_RE = re.compile(r"\b(?:slop|spam|lazy|ai-generated)\b", re.IGNORECASE)


# Comment text is untrusted: no links, images, HTML, code spans or mentions of its own in
# our output. A live `@scout` in a stranger's words would wake an agent in the room.
PLAIN_ESCAPES = str.maketrans(
    {"\\": "\\\\", "`": "\\`", "[": "\\[", "]": "\\]", "<": "&lt;", "|": "\\|", "@": "＠"}
)
AUTOLINK_RE = re.compile(r"\b(https?)://|\b(www)\.", re.IGNORECASE)


def plain(text: str) -> str:
    """Untrusted text on one line, with markdown that could link, embed, mention or break a table defused."""
    flat = " ".join(text.split()).translate(PLAIN_ESCAPES)
    return AUTOLINK_RE.sub(lambda m: f"{m.group(1)}[:]//" if m.group(1) else f"{m.group(2)}[.]", flat)


def cell(text: str) -> str:
    """Make text safe for one markdown table cell."""
    return plain(text)


def mask_verdicts(text: str) -> str:
    """Hide verdict words, even inside quoted comments."""
    return VERDICT_RE.sub("[…]", text)
