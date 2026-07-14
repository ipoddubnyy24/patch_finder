from patch_finder.gitmap import Commit
from patch_finder.resolve import Target
from patch_finder.suspects import diff_relevance, prelanding_verdict, rank


def _target():
    return Target("lustre-rsync-test", "SU", "test_2c", "T2C", None, "master", error_sample="diff")


def test_prelanding_no_change_number(gw):
    assert prelanding_verdict(gw, None, "SU", "T2C") == {
        "tested": False, "failed": False, "fail": 0, "total": 0,
    }


def test_prelanding_failed_and_ignores_other_suites(gw):
    gw.add_session("s1", trigger_job="lustre-reviews")
    gw.add_test_set("ts1", "s1", "SU", status="FAIL")
    gw.add_subtest("ts1", "s1", "T2C", "FAIL")
    gw.add_test_set("ts2", "s1", "OTHER", status="FAIL")   # wrong suite -> ignored
    gw.add_subtest("ts2", "s1", "T2C", "FAIL")
    gw.add_subtest("ts1", "s1", "TX", "PASS")              # wrong subtest -> ignored
    gw.link_review(100, "s1")
    v = prelanding_verdict(gw, 100, "SU", "T2C")
    assert v == {"tested": True, "failed": True, "fail": 1, "total": 1}


def test_prelanding_passed(gw):
    gw.add_session("s2", trigger_job="lustre-reviews")
    gw.add_test_set("ts", "s2", "SU", status="PASS")
    gw.add_subtest("ts", "s2", "T2C", "PASS")
    gw.link_review(200, "s2")
    v = prelanding_verdict(gw, 200, "SU", "T2C")
    assert v == {"tested": True, "failed": False, "fail": 0, "total": 1}


def test_diff_relevance_touches_suite_only():
    score, reasons = diff_relevance(["lustre/changelog.c"], "changelog", "changelog", "")
    assert score == 1.0
    assert reasons == ["touches changelog area"]  # suite token discarded from overlap


def test_diff_relevance_keyword_overlap():
    score, reasons = diff_relevance(
        ["fs/lustre/mdd/mdd_dir.c"], "mdd_dir race", "sanity", ""
    )
    assert score > 0
    assert any("keyword overlap" in r for r in reasons)


def test_diff_relevance_none():
    score, reasons = diff_relevance(["README"], "unrelated", "sanity", "")
    assert score == 0.0 and reasons == []


def test_rank_orders_by_prelanding_verdict(gw):
    # change 100 already failed pre-landing; 200 passed; the third has no change.
    gw.add_session("s1")
    gw.add_test_set("ts1", "s1", "SU", status="FAIL")
    gw.add_subtest("ts1", "s1", "T2C", "FAIL")
    gw.link_review(100, "s1")
    gw.add_session("s2")
    gw.add_test_set("ts2", "s2", "SU", status="PASS")
    gw.add_subtest("ts2", "s2", "T2C", "PASS")
    gw.link_review(200, "s2")

    commits = [
        Commit("passed", "2026-07-02", "A", "s", 200),
        Commit("failed", "2026-07-03", "B", "rsync fix", 100),
        Commit("nochange", "2026-07-04", "C", "s", None),
    ]
    files = {"failed": ["lustre/tests/lustre-rsync-test.sh"], "passed": []}
    suspects = rank(gw, commits, _target(), lambda sha: files.get(sha, []))
    assert [s.commit.sha for s in suspects] == ["failed", "nochange", "passed"]
    assert suspects[0].score == 11.25       # +10 pre-landing fail, +1 touches area, +.25 "rsync" overlap
    assert "pre-landing already FAILED 1/1x" in suspects[0].reasons[0]
    assert "pre-landing PASSED 1x" in suspects[2].reasons[0]
