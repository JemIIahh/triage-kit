"""Plain records for the GitHub data triage-kit reads."""

from dataclasses import dataclass

MAINTAINER_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


@dataclass(frozen=True)
class Comment:
    author: str
    association: str
    body: str
    created_at: str


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    author: str
    association: str
    comments: tuple[Comment, ...]
    labels: tuple[str, ...]


@dataclass(frozen=True)
class IssueSummary:
    number: int
    title: str
    comment_count: int
    labels: tuple[str, ...]


@dataclass(frozen=True)
class PullRequest:
    number: int
    title: str
    body: str
    author: str
    head_ref: str
    url: str
