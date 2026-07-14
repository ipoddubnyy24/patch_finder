"""Detect a step-up in a subtest's failure rate over time (the regression).

Given time-ordered pass/fail samples, find the single change-point that best
splits them into a low-rate "before" and a high-rate "after", and decide
whether that split is real using a permutation test on the likelihood-ratio
statistic.  The permutation test corrects for having scanned every candidate
split, which a naive z-test or Bonferroni bound does not do well for subtle
flaky regressions (e.g. 0% -> 10%).

Deterministic breaks (0% -> 100%) and flaky rate-shifts are the same math: a
deterministic break is just a change-point with a huge likelihood ratio.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Sequence

FAIL_STATUSES = frozenset({"FAIL", "CRASH", "ABORT", "TIMEOUT"})
_TS_KEYS = ("submission", "search_date", "created_at")


@dataclass
class Sample:
    ts: str
    fail: bool
    session_id: str = ""


@dataclass
class Changepoint:
    classification: str  # regression | flaky-stable | clean | insufficient
    index: int | None
    before_rate: float
    after_rate: float
    delta: float
    lr: float
    p_value: float
    n_before: int
    n_after: int
    last_good_ts: str | None
    first_bad_ts: str | None


def to_samples(occurrences: Sequence[dict]) -> list[Sample]:
    """Normalise raw ``sub_tests`` rows into time-ordered pass/fail samples.

    SKIP rows and rows without any timestamp are dropped.
    """
    samples: list[Sample] = []
    for row in occurrences:
        status = (row.get("status") or "").upper()
        if not status or status == "SKIP":
            continue
        ts = next((row[k] for k in _TS_KEYS if row.get(k)), None)
        if not ts:
            continue
        samples.append(
            Sample(
                ts=ts,
                fail=status in FAIL_STATUSES,
                session_id=row.get("test_session_id", "") or "",
            )
        )
    samples.sort(key=lambda s: s.ts)
    return samples


def _binom_ll(successes: int, n: int) -> float:
    if n == 0:
        return 0.0
    p = successes / n
    ll = 0.0
    if successes:
        ll += successes * math.log(p)
    if successes < n:
        ll += (n - successes) * math.log(1 - p)
    return ll


def _best_split(prefix: list[int], total: int, n: int) -> tuple[int, float]:
    """Return (k, log-likelihood) of the best before/after split, 1 <= k < n."""
    best_k, best_ll = 1, -math.inf
    for k in range(1, n):
        ll = _binom_ll(prefix[k], k) + _binom_ll(total - prefix[k], n - k)
        if ll > best_ll:
            best_k, best_ll = k, ll
    return best_k, best_ll


def detect(
    samples: Sequence[Sample],
    min_delta: float = 0.05,
    alpha: float = 0.05,
    min_after: int = 4,
    permutations: int = 1000,
    rng: random.Random | None = None,
) -> Changepoint:
    """Classify the series and locate the transition window.

    ``rng`` defaults to a fixed seed so a given series always yields the same
    verdict — reproducibility matters more than entropy for a diagnostic tool.
    """
    rng = rng or random.Random(0)
    n = len(samples)
    fails = [1 if s.fail else 0 for s in samples]
    total = sum(fails)

    if n < 2 or total == 0:
        rate = (total / n) if n else 0.0
        cls = "clean" if (n and total == 0) else "insufficient"
        first_ts = samples[0].ts if samples else None
        return Changepoint(cls, None, rate, rate, 0.0, 0.0, 1.0, n, 0, first_ts, None)

    prefix = [0]
    for f in fails:
        prefix.append(prefix[-1] + f)

    best_k, best_ll = _best_split(prefix, total, n)
    ll_null = _binom_ll(total, n)
    lr = 2.0 * (best_ll - ll_null)

    # Permutation test: how often does a random relabelling (same total number
    # of failures) produce as strong a best-split as the observed one?
    ge = 0
    perm = fails[:]
    for _ in range(permutations):
        rng.shuffle(perm)
        pp = [0]
        for f in perm:
            pp.append(pp[-1] + f)
        _, bll = _best_split(pp, total, n)
        if 2.0 * (bll - ll_null) >= lr - 1e-9:
            ge += 1
    p_value = (ge + 1) / (permutations + 1)

    n0, n1 = best_k, n - best_k
    before = prefix[best_k] / n0
    after = (total - prefix[best_k]) / n1
    delta = after - before

    before_s = samples[:best_k]
    after_s = samples[best_k:]
    last_good_ts = next((s.ts for s in reversed(before_s) if not s.fail), before_s[0].ts)
    first_bad_ts = next((s.ts for s in after_s if s.fail), after_s[0].ts)

    # delta >= min_delta already implies after > before, so we don't test it twice.
    if delta >= min_delta and p_value < alpha and n1 >= min_after:
        cls = "regression"
    else:
        cls = "flaky-stable"

    return Changepoint(
        cls, best_k, before, after, delta, lr, p_value, n0, n1, last_good_ts, first_bad_ts
    )
