# triage-kit

A small Python library for maintainers whose issues fill up with "can I take this?"
comments and competing pull requests. It reads GitHub through your local `gh` login,
standard library only.

- **Sweep:** find the issues with the most claims and open PRs.
- **Claims:** sort claim comments into bare asks, plans and follow-ups, spot
  near-duplicates, and check the files and functions they mention against the real code.
- **Bench:** run each competing PR's tests in a sealed Docker container and compare them
  side by side. Containers get a scratch copy of the code and nothing else: no credentials,
  no environment, no capabilities, 1 GB of memory. Tests run with no network. Installing
  dependencies does get the network (and can reach your machine's local services), so prefer
  install commands that skip package scripts (`npm ci --ignore-scripts`), and read PRs that
  get the `dependencies changed` flag before you compare them.
  Passing tests don't show code that phones home, so PRs that add network calls or read
  credentials are flagged. A connection attempt the tests print about is flagged too, as a
  hint: quiet code shows nothing there, and the sealed box is what actually stops it.
- **Replies:** draft a thank-you for everyone who wasn't picked, pointing each to an open
  issue nobody is assigned to yet. People who wrote a plan choose first, and each issue goes
  to one person, so the replies don't start a new flood. They go out as a few comments, not
  one per person, so the thread's subscribers aren't flooded either. You post them.
- **Mirror:** copy an issue into a sandbox repo with usernames replaced, for rehearsals.

It never closes, merges or comments on anything by itself. Only `mirror` and
`seed_practice` write, and only to the repo you name.

## Develop

```sh
uv sync
uv run pytest            # docker tests are skipped when Docker isn't running
```

MIT licensed.
