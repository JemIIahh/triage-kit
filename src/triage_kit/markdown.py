"""Small helpers for the markdown triage-kit posts into chat."""

import re

# Words the output never uses: it reports evidence, never a verdict on a person.
VERDICT_RE = re.compile(r"\b(?:slop|spam|lazy|ai-generated)\b", re.IGNORECASE)


def cell(text: str) -> str:
    """Make text safe for one markdown table cell."""
    return " ".join(text.split()).replace("|", "\\|")


def mask_verdicts(text: str) -> str:
    """Hide verdict words, even inside quoted comments."""
    return VERDICT_RE.sub("[…]", text)
