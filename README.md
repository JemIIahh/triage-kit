# triage-kit

A small Python library for maintainers whose issues fill up with "can I take this?"
comments and competing pull requests. It reads GitHub through your local `gh` login,
standard library only.

- **Sweep:** find the issues with the most claims and open PRs.
- **Claims:** sort claim comments into bare asks, plans and follow-ups, spot
  near-duplicates, and check the files and functions they mention against the real code.
- **Bench:** run each competing PR's tests in a sealed Docker container (no network,
  no credentials) and compare them side by side.
- **Mirror:** copy an issue into a sandbox repo with usernames replaced, for rehearsals.

It never closes, merges or comments on anything by itself. Only `mirror` and
`seed_practice` write, and only to the repo you name.

## Develop

```sh
uv sync
uv run pytest            # docker tests are skipped when Docker isn't running
```

MIT licensed.
