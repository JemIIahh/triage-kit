import json
from pathlib import Path

from triage_kit.seed import PRACTICE, seed_practice


def practice_dir(tmp_path: Path) -> Path:
    d = tmp_path / "practice"
    d.mkdir()
    (d / "BASE").write_text("8a3de16\n")
    for name in PRACTICE:
        (d / f"{name}.patch").write_text(f"From x\nSubject: [PATCH] {name}\n")
    return d


class FakeGit:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        self.calls.append(args)
        if "log" in args:
            return f"Title for {self.calls[-3][-1]}\n"
        return ""


class FakeGh:
    def __init__(self):
        self.creates: list[list[str]] = []

    def __call__(self, args: list[str]) -> str:
        if args[:2] == ["pr", "create"]:
            self.creates.append(args)
            return f"https://github.com/me/sandbox/pull/{40 + len(self.creates)}\n"
        if args == ["api", "repos/me/sandbox"]:
            return json.dumps({"default_branch": "master", "private": True})
        raise AssertionError(args)


def test_seed_opens_three_closing_prs(tmp_path):
    sandbox = tmp_path / "sandbox"
    gh, git = FakeGh(), FakeGit()
    numbers = seed_practice(sandbox, "me/sandbox", 7, practice_dir(tmp_path), run=gh, git=git)
    assert numbers == [41, 42, 43]
    heads = [a[a.index("--head") + 1] for a in gh.creates]
    assert heads == ["practice/good", "practice/failing", "practice/sprawling"]
    for a in gh.creates:
        assert a[a.index("--repo") + 1] == "me/sandbox"
        assert a[a.index("--base") + 1] == "master"
        assert a[a.index("--body") + 1].startswith("Closes #7\n\n")
        assert "practice" not in a[a.index("--title") + 1].lower()
    assert all(c[:2] == ["-C", str(sandbox)] for c in git.calls)
    for name in PRACTICE:
        assert ["-C", str(sandbox), "switch", "-C", f"practice/{name}", "8a3de16"] in git.calls
        assert any(c[2] == "am" and c[-1].endswith(f"{name}.patch") for c in git.calls)
        # Pushed to the checked repo by URL, never to whatever `origin` happens to be.
        assert ["-C", str(sandbox), "push", "--force", "https://github.com/me/sandbox.git",
                f"practice/{name}"] in git.calls
    assert git.calls[-1] == ["-C", str(sandbox), "switch", "master"]


def test_seed_refuses_a_public_sandbox(tmp_path):
    class PublicGh(FakeGh):
        def __call__(self, args):
            if args == ["api", "repos/me/sandbox"]:
                return json.dumps({"default_branch": "master", "private": False})
            return super().__call__(args)

    import pytest

    with pytest.raises(ValueError, match="private"):
        seed_practice(tmp_path, "me/sandbox", 7, practice_dir(tmp_path), run=PublicGh(), git=FakeGit())
