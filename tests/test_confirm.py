import pytest

from patch_finder.confirm import ConfirmError, collect, execute, plan
from patch_finder.resolve import Target


def _target():
    return Target("lustre-rsync-test", "SU", "test_2c", "T2C", None, "master")


def _gw_with_target_session(gw):
    gw.add_session("s1", trigger_job="lustre-reviews")
    gw.add_test_set("ts1", "s1", "SU", status="FAIL")
    gw.add_subtest("ts1", "s1", "T2C", "FAIL")
    # a second session for the same change that does NOT run the target suite
    gw.add_session("s2", trigger_job="lustre-reviews")
    gw.add_test_set("ts2", "s2", "OTHER", status="FAIL")
    # a third: right suite, but this run didn't include the target subtest
    gw.add_session("s3", trigger_job="lustre-reviews")
    gw.add_test_set("ts3", "s3", "SU", status="FAIL")
    gw.add_subtest("ts3", "s3", "TX", "PASS")
    gw.link_review(100, "s1")
    gw.link_review(100, "s2")
    gw.link_review(100, "s3")


def test_plan_requires_bug(gw):
    with pytest.raises(ConfirmError, match="justification ticket"):
        plan(gw, 100, _target(), "", 3)


def test_plan_requires_positive_runs(gw):
    with pytest.raises(ConfirmError, match="runs"):
        plan(gw, 100, _target(), "LU-1", 0)


def test_plan_builds_actions_for_matching_sessions_only(gw):
    _gw_with_target_session(gw)
    actions = plan(gw, 100, _target(), "LU-42", 3)
    assert len(actions) == 3                      # only s1 matched, x3 runs
    assert all(a.session_id == "s1" for a in actions)
    assert actions[0].command == [
        "maloo", "retest", gw.session_url("s1"), "LU-42", "--option", "single",
    ]


def test_execute_runs_each_action(gw):
    _gw_with_target_session(gw)
    actions = plan(gw, 100, _target(), "LU-42", 2)
    fired = []

    def runner(cmd):
        fired.append(cmd)
        return {"returncode": 0, "stdout": "queued", "stderr": ""}

    results = execute(actions, runner)
    assert len(results) == 2
    assert results[0]["session_id"] == "s1" and results[0]["returncode"] == 0
    assert len(fired) == 2


def test_plan_respects_max_sessions(gw):
    # two sessions both run the target; cap to the first one only
    gw.add_session("s1", trigger_job="lustre-reviews")
    gw.add_test_set("ts1", "s1", "SU", status="FAIL")
    gw.add_subtest("ts1", "s1", "T2C", "FAIL")
    gw.add_session("sB", trigger_job="lustre-reviews")
    gw.add_test_set("tsB", "sB", "SU", status="FAIL")
    gw.add_subtest("tsB", "sB", "T2C", "FAIL")
    gw.link_review(100, "s1")
    gw.link_review(100, "sB")
    actions = plan(gw, 100, _target(), "LU-1", 2, max_sessions=1)
    assert {a.session_id for a in actions} == {"s1"}
    assert len(actions) == 2


def test_collect_respects_max_sessions(gw):
    _gw_with_target_session(gw)  # s1 has T2C fail; s3 has SU but not T2C
    assert collect(gw, 100, _target(), max_sessions=1)["total"] == 1


def test_collect_delegates_to_verdict(gw):
    _gw_with_target_session(gw)
    assert collect(gw, 100, _target()) == {
        "tested": True, "failed": True, "fail": 1, "total": 1,
    }
