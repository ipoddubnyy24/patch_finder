# patch_finder

Find the Lustre patch that introduced a [Maloo](https://testing.whamcloud.com)
test regression — **mostly without rebuilding anything.**

When a test starts failing on a branch, the offending patch is somewhere in the
commits that landed since it last passed. The classic way to find it is
`git bisect` with fresh builds — but a Lustre build takes tens of minutes and a
test needs a multi-node cluster, so bisecting a flaky failure is punishingly
slow.

`patch_finder` takes a shortcut: Maloo already stores test results for *every
version of every patch*. So instead of rebuilding, it **mines that existing
data** — reconstructs the failing subtest's pass/fail history, finds the point
in time where the failure rate stepped up, maps that window to the commits that
landed then, and ranks them by each patch's *own* pre-landing verdict. Only if
the data is genuinely inconclusive does it offer an active step, and even then
it re-runs existing CI rather than compiling locally.

---

## Contents

- [How it works](#how-it-works)
- [Install](#install)
- [Quick start](#quick-start)
- [Commands](#commands)
- [Interpreting a bisect](#interpreting-a-bisect)
- [JSON output](#json-output)
- [Configuration](#configuration)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Development](#development)

---

## How it works

```
1. Target      a failing run (Maloo URL) or (suite, test, job)        resolve.py
2. Confirm     per-config fail-rate over time; change-point + p-value  timeline.py
               (a step-up = regression; steady = just flaky)
3. Map         transition window -> commits that landed then           gitmap.py
               (Reviewed-on trailer -> Gerrit change number)
4. Rank        each suspect's own pre-landing Maloo verdict + diff      suspects.py
5. Confirm*    (optional) re-run a suspect's sessions K times           confirm.py
```

Steps 1–4 are offline and finish in seconds. Step 5 is opt-in and asynchronous.

**The change-point test.** Each run of the target subtest is a pass/fail
(Bernoulli) sample. `patch_finder` finds the split that best divides the series
into a low-rate "before" and high-rate "after", then runs a **permutation test**
on the likelihood-ratio statistic to get a p-value that already accounts for
having scanned every candidate split. This catches both hard breaks (0%→100%)
and subtle flaky regressions (0%→~10%) without the false positives a naive
per-split z-test would produce. It is deterministic (fixed RNG seed): the same
data always yields the same verdict.

---

## Install

```bash
git clone https://github.com/ipoddubnyy24/patch_finder
cd patch_finder
python3.11 -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'      # drop [dev] if you don't need the tests
```

### Prerequisites

| Need | Detail |
|------|--------|
| **Maloo credentials** | `$MALOO_USER` / `$MALOO_PASS`, or `~/.config/maloo-tool/.env` (the same file the `maloo` CLI uses). The env wins if both are set. Nothing is stored in this repo. |
| **A local `ex/lustre-release` clone** | For the commit → change mapping. Default `~/work/src/lustre/lustre-release`; override with `--clone`. Keep the branch you bisect current (`--fetch`, or a fresh clone). |
| **The `maloo` CLI on `$PATH`** | Only for `confirm --execute`, which shells out to `maloo retest`. Not needed for `scan`/`bisect`. |

---

## Quick start

```bash
# 1. What's failing most on a branch right now?
patch_finder scan --job b_es6_0

# 2. Bisect one of those failures (paste its Maloo URL, or name it)
patch_finder bisect --suite lustre-rsync-test --test test_2c --job b_es6_0 --fetch

# 3. Not sure the top suspect is really the cause? Sample its true fail-rate.
patch_finder confirm --change 67068 --suite lustre-rsync-test --test test_2c \
    --job b_es6_0 --bug LU-19487 --runs 20 --execute
```

Add `--json` to any command for a machine-readable envelope.

---

## Commands

### `scan` — find candidates

Ranks a branch's most frequent recent failures. Start here when you know a
branch is unhealthy but not which test regressed. Every candidate comes with a
ready-to-run `bisect` command.

```bash
patch_finder scan --job b_es6_0                 # last 10 days
patch_finder scan --job b_es7_0 --days 14 --top 10
```

```text
job lustre-b_es6_0   window 2026-07-05..2026-07-15   sessions_examined=48

  1. lustre-rsync-test test_2c   x27 in 9 sessions
        Failure in replication; differences found.
        patch_finder bisect --url https://testing.whamcloud.com/test_sets/16638759-936b-4590-add8-bdb5bd0eb287
  2. sanity test_413b   x11 in 7 sessions
        test_413b failed: unexpected free-space imbalance
        patch_finder bisect --url https://testing.whamcloud.com/test_sets/8b1c...-...
  3. recovery-small test_136   x4 in 4 sessions
        patch_finder bisect --url https://testing.whamcloud.com/test_sets/2f0a...-...
```

| option | default | meaning |
|--------|---------|---------|
| `--job` | *(required)* | Branch name (`b_es6_0`) or job name (`lustre-reviews`). |
| `--days` | 10 | Look-back window. |
| `--top` | 20 | Candidates to show. |
| `--max-sessions` | 400 | Session-scan cap (warns if hit). |

### `bisect` — find the offending patch

Point it at a failing run's Maloo URL, or name the `(suite, test, job)`.

```bash
# from a failing run's URL (suite/test/config auto-detected)
patch_finder bisect --url https://testing.whamcloud.com/test_sets/<uuid>

# by name, scoped to a branch and one config (recommended)
patch_finder bisect --suite lustre-rsync-test --test test_2c \
    --job b_es6_0 --config fs=ldiskfs --days 21 --fetch
```

```text
target: lustre-rsync-test test_2c  (job=lustre-b_es6_0, base=b_es6_0)
  config: server_fs=ldiskfs, client_distro=RHEL 8.10, arch=x86_64
  error : Failure in replication; differences found.

verdict: REGRESSION   (window 2026-07-08..2026-07-14)
  samples=142 fails=27   rate 0.0% -> 34.1% (delta +34.1pt, p=0.001, LR=58.902)
  transition: last-good 2026-07-10T04:12:07.000Z  ->  first-bad 2026-07-10T09:48:55.000Z

suspects (ranked, 6):
  1. [+11.0] 40fc600b30d1 change 67068  LU-20388 pcc: fix failed PCC mmap after unmap
        - pre-landing already FAILED 3/8x
        - touches lustre-rsync-test area
  2. [+0.2] 456c7a8f19aa change 66835  LU-20383 tests: fix stripe retrieval in sanity
        - keyword overlap: stripe
  3. [-2.0] 062270928c33 change 67036  EX-14806 kernel: decode 400GbE eeprom info
        - pre-landing PASSED 5x

confirm the top suspect:
  patch_finder confirm --change 67068 --suite lustre-rsync-test --test test_2c --job lustre-b_es6_0 --bug LU-XXXXX --runs 20 --execute
```

| option | default | meaning |
|--------|---------|---------|
| `--url` | — | Maloo `test_set`/`test_session` URL or bare UUID. |
| `--suite` / `--test` | — | Name the target instead of a URL. |
| `--job` | *(from URL if given)* | Branch or job to scope the history to. **Strongly recommended** — see [Troubleshooting](#troubleshooting). |
| `--branch` | *(inferred)* | Override the base git branch to walk. |
| `--config` | — | Restrict to one config, e.g. `fs=ldiskfs,distro=RHEL 8.10` (keys: `fs`, `distro`, `arch`, `group`). |
| `--days` | 14 | Look-back window. |
| `--clone` | `~/work/src/lustre/lustre-release` | Local `ex/lustre-release` clone. |
| `--fetch` | off | `git fetch` the branch before mapping. |

### `confirm` — actively confirm a flaky suspect

Requeues the suspect change's existing sessions `K` times via `maloo retest` to
measure its true fail-rate. **Dry-run by default** — it prints the exact commands
and fires nothing. `--execute` fires them and requires a justification ticket.

```bash
# preview (no ticket needed)
patch_finder confirm --change 67068 --suite lustre-rsync-test --test test_2c --job b_es6_0 --runs 20
```

```text
confirm change 67068: lustre-rsync-test test_2c  mode=dry-run
  would fire 20 retest(s) (use --execute):
    maloo retest https://testing.whamcloud.com/test_sessions/<uuid> LU-XXXXX --option single
    ... (x20)
warning: dry-run using placeholder ticket LU-XXXXX; pass --bug for real
```

```bash
# fire them for real, then read the result once they finish (hours later)
patch_finder confirm --change 67068 --suite lustre-rsync-test --test test_2c --job b_es6_0 --bug LU-19487 --runs 20 --execute
patch_finder confirm --change 67068 --suite lustre-rsync-test --test test_2c --job b_es6_0 --collect
```

```text
confirm change 67068: lustre-rsync-test test_2c  mode=collect
  current tally: 7/28 failing (25.0%)
```

| option | default | meaning |
|--------|---------|---------|
| `--change` | *(required)* | Gerrit change number of the suspect. |
| `--suite`/`--test`/`--job` or `--url` | — | The target (same resolution as `bisect`). |
| `--bug` | — | Justification ticket. **Required with `--execute`.** |
| `--runs` | 20 | How many retests to fire. |
| `--execute` | off | Actually fire (default is dry-run). |
| `--collect` | off | Read the current fail-rate instead of retesting. |

---

## Interpreting a bisect

**Verdict** is one of:

| verdict | meaning | what to do |
|---------|---------|------------|
| `REGRESSION` | The failure rate stepped up within the window (`p` below threshold). | Look at the ranked suspects. |
| `FLAKY-STABLE` | Failures exist but show no clear step — persistent flakiness, or too little data to be sure. | Suspects are weak leads; use `confirm` to measure a suspect's real rate. |
| `CLEAN` | No failures in the window. | Not a regression, or wrong target/window. |
| `INSUFFICIENT` | Fewer than two usable samples. | Widen `--days`, or check the suite/test/job. |

**Suspects** are ranked by score. The dominant signal is a patch's *own*
pre-landing verdict: a patch that already failed this exact `(suite, test)` in
its review testing scores **+10** and floats to the top; one that passed scores
**−2**. A small diff-relevance bonus (touching the test's area, keyword overlap
with the failure) breaks ties. The `reasons` under each suspect explain the
score. A busy window yields several suspects — that's expected; the ranking and
`confirm` narrow it down.

---

## JSON output

`--json` wraps the result in a stable envelope (`{ok, data, meta}`):

```json
{
  "ok": true,
  "data": {
    "target": {"suite": "lustre-rsync-test", "test": "test_2c", "job": "lustre-b_es6_0",
               "base_branch": "b_es6_0", "config": {"server_fs": "ldiskfs", "...": "..."},
               "error_sample": "Failure in replication; differences found."},
    "window": {"from": "2026-06-23", "to": "2026-07-14"},
    "analysis": {"classification": "regression", "samples": 142, "fails": 27,
                 "before_rate": 0.0, "after_rate": 0.341, "delta": 0.341,
                 "lr": 58.902, "p_value": 0.001, "n_before": 60, "n_after": 82,
                 "last_good": "2026-07-10T04:12:07.000Z", "first_bad": "2026-07-10T09:48:55.000Z"},
    "suspects": [
      {"sha": "40fc600b30d1...", "change_number": 67068, "subject": "LU-20388 pcc: ...",
       "author": "...", "committed": "2026-07-10T...", "score": 11.0,
       "prelanding": {"tested": true, "failed": true, "fail": 3, "total": 8},
       "reasons": ["pre-landing already FAILED 3/8x", "touches lustre-rsync-test area"]}
    ],
    "note": "",
    "recommended_confirm": "patch_finder confirm --change 67068 ...",
    "warnings": []
  },
  "meta": {"tool": "patch_finder", "command": "bisect"}
}
```

---

## Configuration

| What | Where | Notes |
|------|-------|-------|
| Maloo auth | `$MALOO_USER`/`$MALOO_PASS` or `~/.config/maloo-tool/.env` | Also honours `$MALOO_URL` (default `https://testing.whamcloud.com`). |
| Lustre clone | `--clone` or default `~/work/src/lustre/lustre-release` | Must be a checkout of `ex/lustre-release` with the branch you bisect. |

---

## Troubleshooting

**`REGRESSION` but no suspects listed.** The git-mapping step found no commits in
the transition window — almost always because the clone's branch is stale. A real
example against a stale `origin/master`:

```text
verdict: REGRESSION   (window 2026-07-09..2026-07-14)
  samples=3000 fails=496   rate 5.0% -> 28.6% (delta +23.6pt, p=0.001, LR=326.397)
warning: suite-run fetch hit the 3000 cap; history may be truncated
```

Fix: pass `--fetch`, or point `--clone` at a current checkout. (The DDN branches
`b_es6_0`/`b_es7_0` are usually fresher than the `master` mirror.)

**The verdict looks noisy / "regression" is unconvincing.** You probably ran
without `--job`, which mixes every branch, distro and filesystem into one series
and can trip the suite-run cap. Always pass `--job` (and ideally `--config`) so
the series reflects one real pipeline.

**`multiple failing subtests; pass --test one of: ...`.** The `test_set` URL had
more than one failing subtest; re-run with `--test <name>`.

**`Maloo API request to '...' failed: ... timed out`.** A single query was too
heavy — usually a `scan` over a very high-volume job (`lustre-reviews`). Narrow
`--days` or lower `--max-sessions`. (For `bisect` this is designed out: it never
enumerates every PASS of a common subtest.)

**`Maloo credentials not found`.** Set `$MALOO_USER`/`$MALOO_PASS` or create
`~/.config/maloo-tool/.env`.

**`branch not found in clone`.** The `--branch`/base isn't in your clone. Fetch it
or pick another with `--branch`.

---

## Limitations

- The failure *rate* uses the number of suite runs as the denominator (Maloo
  can't cheaply enumerate every PASS of a common subtest), so a run that SKIPs
  the subtest counts as a pass — negligible for most tests.
- Commit windowing uses committer date as a proxy for land time; a busy day
  yields several suspects (that's what the ranking is for).
- `scan` and review-job history walk sessions and are capped (with a warning)
  on very high-volume jobs like `lustre-reviews`.
- Patch-interaction regressions (A and B each fine, A+B broken) won't show a
  single pre-landing culprit — the change-point still localises the window.

---

## Development

```bash
pip install -e '.[dev]'
pytest                    # 100% unit coverage, no network required
```

Layout (`src/patch_finder/`):

| module | responsibility |
|--------|----------------|
| `cli.py` | argument parsing → pipeline → render (thin) |
| `maloo.py` | date-bounded Maloo API gateway (injectable HTTP) |
| `resolve.py` | URL/`(suite,test)` → resolved target (pins the right script-ids) |
| `timeline.py` | change-point detection with permutation test |
| `gitmap.py` | commits-in-window and `Reviewed-on` → Gerrit change |
| `suspects.py` | pre-landing verdict + diff-relevance ranking |
| `confirm.py` | plan / fire / collect `maloo retest` |
| `pipeline.py` | orchestration (`run_bisect`, `run_scan`) |
| `report.py` | human text + JSON envelope |

Every module is injectable (gateway, git runner, clone path), so the whole tool
is tested against in-memory fakes with no network or real repo.
