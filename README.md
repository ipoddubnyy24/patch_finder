# patch_finder

Find the Lustre patch that introduced a [Maloo](https://testing.whamcloud.com)
test regression — **mostly without rebuilding anything.**

Maloo already stores test results for every version of every patch, so instead
of `git bisect`-ing with fresh builds (hours per step, needs a cluster),
`patch_finder` mines that existing data: it reconstructs a failing subtest's
pass/fail history, finds the point in time where the failure rate stepped up,
maps that window to the commits that landed in it, and ranks them by each
patch's *own* pre-landing verdict. Only if the data is genuinely inconclusive
does it offer an active step, and even then it re-runs existing CI rather than
compiling locally.

## Method

```
1. Target      URL of a failing run, or (suite, test, job)          -> resolve.py
2. Confirm     per-config fail-rate over time; change-point test    -> timeline.py
               (regression vs steady flakiness, with a p-value)
3. Map         transition window -> landed commits + Gerrit change# -> gitmap.py
4. Rank        each suspect's pre-landing Maloo verdict + diff match -> suspects.py
5. Confirm*    (optional) re-run a suspect's sessions K times        -> confirm.py
```

Steps 1–4 are offline and finish in seconds. Step 5 is opt-in and asynchronous.

## Install

```bash
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
```

### Prerequisites

- **Maloo credentials** — read from `$MALOO_USER` / `$MALOO_PASS` or
  `~/.config/maloo-tool/.env` (the same file the `maloo` CLI uses). Nothing is
  stored in this repo.
- **A local `ex/lustre-release` clone** for the commit mapping
  (default `~/work/src/lustre/lustre-release`, override with `--clone`).
- **The `maloo` CLI on `$PATH`** — only for `confirm --execute`, which shells
  out to `maloo retest`.

## Usage

```bash
# 1a — what's failing most on a branch right now?
patch_finder scan --job b_es6_0 --days 10

# 1b — bisect a specific failure (from its Maloo URL)
patch_finder bisect --url https://testing.whamcloud.com/test_sets/<uuid>

#      or by name, scoped to a branch and config
patch_finder bisect --suite lustre-rsync-test --test test_2c \
    --job b_es6_0 --config fs=ldiskfs --days 21

# 5 — confirm the top suspect is really flaky (dry-run prints the commands)
patch_finder confirm --change 65428 --suite lustre-rsync-test --test test_2c \
    --job b_es6_0 --bug LU-XXXXX --runs 20            # add --execute to fire
patch_finder confirm --change 65428 --suite lustre-rsync-test --test test_2c \
    --job b_es6_0 --collect                            # read the new fail-rate later
```

Add `--json` to any command for a `{ok, data, meta}` envelope.

## How the change-point test works

Each run of the target subtest is a Bernoulli sample (pass/fail). `patch_finder`
finds the split maximising the before/after likelihood, then runs a
**permutation test** on the likelihood-ratio statistic to get a scan-corrected
p-value. This catches both hard breaks (0%→100%) and subtle flaky regressions
(0%→~10%) without the false positives a naive per-split z-test would produce.
The result is deterministic (fixed RNG seed) so the same data always gives the
same verdict.

## Limitations

- The failure *rate* uses the number of suite runs as the denominator (Maloo
  can't cheaply enumerate every PASS of a common subtest), so a run that SKIPs
  the subtest is counted as a pass — negligible for most tests.
- Commit windowing uses committer date as a proxy for land time; a busy day
  yields several suspects (that's what the ranking is for).
- `scan` and review-job history walk sessions and are capped (with a warning)
  on very high-volume jobs like `lustre-reviews`.
- Patch-interaction regressions (A and B each fine, A+B broken) won't show a
  single pre-landing culprit — the change-point still localises the window.

## Development

```bash
pytest          # 100% unit coverage, no network required
```
