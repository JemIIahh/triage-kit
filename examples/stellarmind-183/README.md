# Rehearsal: three practice PRs for stellarmind#183

[Flamki/stellarmind#183](https://github.com/Flamki/stellarmind/issues/183) asks for a
verified backup and restore for run history. These are three practice PRs for it,
written for rehearsing a PR comparison. None was ever sent to the real project.

| Patch | What it is | Expected comparison |
|---|---|---|
| `good.patch` | Backup, verify and restore with a checksum, a CLI, tests and docs | tests pass, nothing out of scope |
| `failing.patch` | The same idea, but verify skips the checksum | `history-backup.test.js` fails on the damaged-backup case |
| `sprawling.patch` | The good change plus README, `package.json`, `.prettierrc` and CI edits | tests pass, 4 files out of scope |

`BASE` is the stellarmind commit they apply to (`git am`). In a private copy of
stellarmind:

```python
from pathlib import Path
from triage_kit.seed import seed_practice
seed_practice(Path("~/sandbox").expanduser(), "you/stellarmind-sandbox", ISSUE, Path("examples/stellarmind-183"))
```

Compare with scope `src/storage/**`, `tests/**`, `docs/**`, image `node:20-slim`,
install `npm ci --ignore-scripts --no-audit --no-fund` and tests
`node tests/run-history-schema.test.js && node tests/docker-run-history.test.js && node tests/history-backup.test.js`.
(stellarmind's own `tests/run-history-recovery.test.js` already fails at `BASE`, and its
full `npm test` needs the network.)

The patches are derived from stellarmind, which is MIT licensed (Copyright (c) Flamki).
