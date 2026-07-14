import random

from patch_finder.timeline import Sample, _binom_ll, detect, to_samples


def mk(pattern):
    """Build ordered samples from a list of 0/1 (pass/fail)."""
    return [Sample(ts=f"t{i:04d}", fail=bool(v)) for i, v in enumerate(pattern)]


# -- to_samples ---------------------------------------------------------

def test_to_samples_filters_sorts_and_maps():
    rows = [
        {"status": "FAIL", "submission": "2026-07-03", "test_session_id": "s3"},
        {"status": "PASS", "submission": "2026-07-01"},
        {"status": "SKIP", "submission": "2026-07-02"},        # dropped
        {"status": "", "submission": "2026-07-02"},            # dropped (no status)
        {"status": "PASS"},                                    # dropped (no ts)
        {"status": "pass", "search_date": "2026-07-02"},       # search_date fallback + lowercase
    ]
    samples = to_samples(rows)
    assert [s.ts for s in samples] == ["2026-07-01", "2026-07-02", "2026-07-03"]
    assert [s.fail for s in samples] == [False, False, True]
    assert samples[2].session_id == "s3"


# -- _binom_ll edge cases ----------------------------------------------

def test_binom_ll_branches():
    assert _binom_ll(0, 0) == 0.0
    assert _binom_ll(5, 5) == 0.0      # p=1
    assert _binom_ll(0, 5) == 0.0      # p=0
    assert _binom_ll(2, 5) < 0.0       # genuine mix


# -- detect: trivial series --------------------------------------------

def test_detect_insufficient_empty():
    cp = detect([])
    assert cp.classification == "insufficient"
    assert cp.index is None and cp.last_good_ts is None


def test_detect_single_pass_is_clean():
    cp = detect(mk([0]))
    assert cp.classification == "clean"


def test_detect_single_fail_is_insufficient():
    cp = detect(mk([1]))
    assert cp.classification == "insufficient"


def test_detect_all_pass_is_clean():
    cp = detect(mk([0, 0, 0, 0]))
    assert cp.classification == "clean"
    assert cp.before_rate == 0.0 and cp.after_rate == 0.0


# -- detect: regressions ------------------------------------------------

def test_detect_deterministic_regression():
    cp = detect(mk([0] * 10 + [1] * 10), permutations=200)
    assert cp.classification == "regression"
    assert cp.index == 10
    assert cp.before_rate == 0.0 and cp.after_rate == 1.0
    assert cp.last_good_ts == "t0009" and cp.first_bad_ts == "t0010"
    assert cp.p_value < 0.05


def test_detect_flaky_regression():
    after = [1, 0] * 15  # 50% fail
    cp = detect(mk([0] * 30 + after), permutations=200, rng=random.Random(1))
    assert cp.classification == "regression"
    assert cp.after_rate > cp.before_rate


# -- detect: not regressions -------------------------------------------

def test_detect_steady_flaky_is_not_a_regression():
    cp = detect(mk([1, 0] * 20), permutations=300)
    assert cp.classification == "flaky-stable"


def test_detect_recovery_hits_default_window_edges():
    # 10 fail then 10 pass: before all-fail, after all-pass -> both fallbacks.
    cp = detect(mk([1] * 10 + [0] * 10), permutations=200)
    assert cp.classification == "flaky-stable"
    assert cp.last_good_ts == "t0000"   # before group had no passing sample
    assert cp.first_bad_ts == "t0010"   # after group had no failing sample


def test_detect_min_after_guard():
    cp = detect(mk([0] * 20 + [1] * 3), permutations=200)
    assert cp.classification == "flaky-stable"  # only 3 after-samples < min_after
    assert cp.delta == 1.0


def test_detect_is_deterministic():
    s = mk([0] * 10 + [1] * 10)
    assert detect(s, permutations=200).p_value == detect(s, permutations=200).p_value
