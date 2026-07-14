import pytest

from patch_finder.resolve import (
    ResolveError,
    Target,
    job_to_base_branch,
    parse_ref,
    resolve_target,
)

TSID = "16638759-936b-4590-add8-bdb5bd0eb287"
SID = "a22a650a-38a5-46bf-8de8-c055495d52c2"


def _base(gw):
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    gw.name_sub_script("T2C2", "test_2c")   # same name, different id (the trap)
    gw.name_sub_script("TX", "test_9a")
    gw.add_session(
        SID, trigger_job="lustre-reviews", server_file_system="zfs",
        client_distribution="RHEL 8.10", client_architecture="x86_64",
        test_group="review-dne", server_branch="master",
    )


# -- parse_ref / job_to_base_branch ------------------------------------

def test_parse_ref_variants():
    assert parse_ref(f"https://x/test_sets/{TSID}") == ("test_sets", TSID)
    assert parse_ref(f"https://x/test_sessions/{SID}") == ("test_sessions", SID)
    assert parse_ref(TSID) == (None, TSID)
    with pytest.raises(ResolveError):
        parse_ref("not-a-ref")


@pytest.mark.parametrize(
    "job,branch",
    [
        (None, "master"),
        ("lustre-reviews", "master"),
        ("lustre-master", "master"),
        ("lustre-b_es6_0", "b_es6_0"),
        ("b_es6_0", "b_es6_0"),
        ("lustre-b_es-reviews", "master"),
    ],
)
def test_job_to_base_branch(job, branch):
    assert job_to_base_branch(job) == branch


# -- test_set URL ------------------------------------------------------

def test_resolve_test_set_single_failure(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL", error="boom")
    gw.add_subtest(TSID, SID, "TX", "PASS")
    t = resolve_target(gw, url=f"https://x/test_sets/{TSID}")
    assert (t.suite, t.test, t.sub_test_script_id) == ("lustre-rsync-test", "test_2c", "T2C")
    assert t.job == "lustre-reviews" and t.base_branch == "master"
    assert t.error_sample == "boom"
    assert t.config["server_fs"] == "zfs"


def test_resolve_test_set_explicit_test_and_job_override(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL", error="boom")
    t = resolve_target(gw, url=f"https://x/test_sets/{TSID}", test="test_2c", job="lustre-master")
    assert t.test == "test_2c" and t.job == "lustre-master"


def test_resolve_test_set_test_not_present(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL")
    with pytest.raises(ResolveError, match="not found"):
        resolve_target(gw, url=f"https://x/test_sets/{TSID}", test="test_zz")


def test_resolve_test_set_no_failure(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="PASS")
    gw.add_subtest(TSID, SID, "TX", "PASS")
    with pytest.raises(ResolveError, match="no failing"):
        resolve_target(gw, url=f"https://x/test_sets/{TSID}")


def test_resolve_test_set_multiple_failures(gw):
    _base(gw)
    gw.name_sub_script("TB", "test_5")
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL")
    gw.add_subtest(TSID, SID, "TB", "FAIL")
    with pytest.raises(ResolveError, match="multiple failing"):
        resolve_target(gw, url=f"https://x/test_sets/{TSID}")


def test_resolve_test_set_not_found(gw):
    with pytest.raises(ResolveError, match="not found"):
        resolve_target(gw, url=f"https://x/test_sets/{TSID}")


# -- session URL -------------------------------------------------------

def test_resolve_session_pins_from_session(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL")
    t = resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="test_2c")
    assert t.sub_test_script_id == "T2C" and t.base_branch == "master"


def test_resolve_session_falls_back_to_recent(gw):
    _base(gw)
    # session has a test_set of a *different* suite, so pinning falls back to
    # scanning recent runs of the wanted suite (TSID, elsewhere).
    gw.name_set_script("OTHER", "sanity")
    gw.add_test_set("other-ts", SID, "OTHER", status="PASS")
    gw.add_test_set(TSID, "some-other-session", "SU", status="FAIL")
    gw.add_subtest(TSID, "some-other-session", "T2C", "FAIL")
    t = resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="test_2c")
    assert t.sub_test_script_id == "T2C"


def test_resolve_session_suite_present_but_subtest_missing(gw):
    _base(gw)
    gw.add_test_set("sess-su", SID, "SU", status="FAIL")
    gw.add_subtest("sess-su", SID, "TX", "PASS")   # right suite in-session, but not test_2c
    gw.add_test_set("recent", "elsewhere", "SU", status="FAIL")
    gw.add_subtest("recent", "elsewhere", "T2C", "FAIL")   # found via recent-runs fallback
    t = resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="test_2c")
    assert t.sub_test_script_id == "T2C"


def test_resolve_session_requires_suite_and_test(gw):
    _base(gw)
    with pytest.raises(ResolveError, match="needs --suite and --test"):
        resolve_target(gw, url=f"https://x/test_sessions/{SID}")


def test_resolve_session_unknown_suite(gw):
    _base(gw)
    with pytest.raises(ResolveError, match="unknown suite"):
        resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="ghost", test="test_2c")


def test_resolve_session_not_found(gw):
    with pytest.raises(ResolveError, match="not found"):
        resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="test_2c")


def test_resolve_pin_unknown_subtest(gw):
    _base(gw)
    gw.add_test_set("ts", "other", "SU", status="FAIL")
    with pytest.raises(ResolveError, match="no subtest named"):
        resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="ghost_test")


def test_resolve_pin_not_in_recent_runs(gw):
    _base(gw)
    gw.add_test_set("ts", "other", "SU", status="FAIL")
    gw.add_subtest("ts", "other", "TX", "PASS")   # suite runs, but never test_2c
    with pytest.raises(ResolveError, match="could not find"):
        resolve_target(gw, url=f"https://x/test_sessions/{SID}", suite="lustre-rsync-test", test="test_2c")


# -- no URL ------------------------------------------------------------

def test_resolve_no_url(gw):
    _base(gw)
    gw.add_test_set(TSID, "other", "SU", status="FAIL")
    gw.add_subtest(TSID, "other", "T2C", "FAIL")
    t = resolve_target(gw, suite="lustre-rsync-test", test="test_2c", job="lustre-b_es6_0")
    assert t.sub_test_script_id == "T2C" and t.base_branch == "b_es6_0"


def test_resolve_no_url_requires_suite_test(gw):
    with pytest.raises(ResolveError, match="must pass --suite and --test"):
        resolve_target(gw, job="lustre-master")


def test_resolve_no_url_unknown_suite(gw):
    with pytest.raises(ResolveError, match="unknown suite"):
        resolve_target(gw, suite="ghost", test="test_2c", job="lustre-master")


# -- bare UUID + base override -----------------------------------------

def test_resolve_bare_uuid_test_set(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL")
    t = resolve_target(gw, url=TSID)
    assert t.test == "test_2c"


def test_resolve_bare_uuid_session(gw):
    _base(gw)
    gw.add_test_set("ts", SID, "SU", status="FAIL")
    gw.add_subtest("ts", SID, "T2C", "FAIL")
    t = resolve_target(gw, url=SID, suite="lustre-rsync-test", test="test_2c")
    assert t.sub_test_script_id == "T2C"


def test_resolve_base_branch_override(gw):
    _base(gw)
    gw.add_test_set(TSID, SID, "SU", status="FAIL")
    gw.add_subtest(TSID, SID, "T2C", "FAIL")
    t = resolve_target(gw, url=f"https://x/test_sets/{TSID}", base_branch="b_es7_0")
    assert t.base_branch == "b_es7_0"


def test_target_is_dataclass():
    t = Target("s", "sid", "t", "stid", None, "master")
    assert t.config == {}
