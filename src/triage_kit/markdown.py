"""Small helpers for the markdown triage-kit posts into chat."""

import re

# Words the output never uses: it reports evidence, never a verdict on a person.
VERDICT_RE = re.compile(r"\b(?:slop|spam|lazy|ai-generated)\b", re.IGNORECASE)


# Comment text is untrusted: no links, images, HTML or code spans of its own in our output.
PLAIN_ESCAPES = str.maketrans({"\\": "\\\\", "`": "\\`", "[": "\\[", "]": "\\]", "<": "&lt;", "|": "\\|"})


def plain(text: str) -> str:
    """Untrusted text on one line, with markdown that could link, embed or break a table escaped."""
    return " ".join(text.split()).translate(PLAIN_ESCAPES)


def cell(text: str) -> str:
    """Make text safe for one markdown table cell."""
    return plain(text)


def mask_verdicts(text: str) -> str:
    """Hide verdict words, even inside quoted comments."""
    return VERDICT_RE.sub("[…]", text)
