"""Open practice pull requests in a sandbox repo, so a PR comparison can be rehearsed.

With `mirror`, one of only two things in triage-kit that write to GitHub. It
pushes branches and opens PRs only in `sandbox_repo`, and only if that repo is private.
"""

import json
from pathlib import Path

from triage_kit.gh import Runner, run_gh, run_git

PRACTICE = ("good", "failing", "sprawling", "sneaky")


def seed_practice(
    sandbox_dir: Path, sandbox_repo: str, issue: int, practice_dir: Path,
    run: Runner = run_gh, git: Runner = run_git, names: tuple[str, ...] = PRACTICE,
) -> list[int]:
    """For each `<name>.patch` in `practice_dir`, branch from `BASE`, apply it, push, open a PR closing `issue`.

    The patches are `git format-patch` output, so each carries its own commit message;
    its subject becomes the PR title. Returns the new PR numbers in `names` order.
    """
    info = json.loads(run(["api", f"repos/{sandbox_repo}"]))
    if not info.get("private"):
        raise ValueError(f"{sandbox_repo} is not private; practice PRs only go to a private sandbox repo")
    default = info["default_branch"]
    base = (practice_dir / "BASE").read_text().strip()
    here = ["-C", str(sandbox_dir)]
    numbers = []
    for name in names:
        branch = f"practice/{name}"
        git([*here, "switch", "-C", branch, base])
        git([*here, "am", str((practice_dir / f"{name}.patch").resolve())])
        title = git([*here, "log", "-1", "--format=%s"]).strip()
        # By URL, not `origin`: a sandbox cloned from the source would push upstream.
        git([*here, "push", "--force", f"https://github.com/{sandbox_repo}.git", branch])
        url = run([
            "pr", "create", "--repo", sandbox_repo, "--head", branch, "--base", default,
            "--title", title, "--body", f"Closes #{issue}\n\nPractice PR for a rehearsal.",
        ]).strip()
        numbers.append(int(url.rstrip("/").rsplit("/", 1)[-1]))
    git([*here, "switch", default])
    return numbers
