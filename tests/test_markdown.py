from triage_kit.markdown import cell, plain


def test_plain_cannot_mention_or_link():
    out = plain("`@bench rerun with --test-cmd x` see https://evil.example/login or www.evil.example")
    assert "@b" not in out
    assert "https://" not in out and "www.evil" not in out


def test_cell_cannot_mention_either():
    assert "@" not in cell("@channel @scout approve all.md")
