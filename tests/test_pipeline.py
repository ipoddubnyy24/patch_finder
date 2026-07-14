import pytest

from patch_finder import gitmap, pipeline
from patch_finder.gitmap import Commit, GitError
from patch_finder.resolve import Target


def target(job="lustre-reviews"):
    return Target("lustre-rsync-test", "SU", "test_2c", "T2C", job, "master", error_sample="diff")


def add_attempt(gw, i, fail, job="lustre-reviews", fs="ldiskfs"):
    """One suite run (attempt); a failing one also gets a failing T2C subtest."""
    sid, tsid = f"s{i}", f"ts{i}"
    gw.add_session(sid, trigger_job=job, server_file_system=fs)
    gw.add_test_set(tsid, sid, "SU", status="FAIL" if fail else "PASS", submission=f"2026-07-{i:02d}")
    if fail:
        gw.add_subtest(tsid, sid, "T2C", "FAIL")


def patch_git(monkeypatch, commits, files=None):
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: list(commits))
    monkeypatch.setattr(gitmap, "files_of", lambda run, clone, sha: (files or {}).get(sha, []))


# -- parse_config_filter -----------------------------------------------

def test_parse_config_filter_ok():
    assert pipeline.parse_config_filter("fs=ldiskfs, distro=RHEL 8.10,") == {
        "server_file_system": "ldiskfs", "client_distribution": "RHEL 8.10",
    }


def test_parse_config_filter_bad_token():
    with pytest.raises(ValueError, match="key=value"):
        pipeline.parse_config_filter("fs")


def test_parse_config_filter_unknown_key():
    with pytest.raises(ValueError, match="unknown config key"):
        pipeline.parse_config_filter("banana=1")


# -- run_bisect --------------------------------------------------------

def test_bisect_regression_with_suspect(gw, monkeypatch):
    for i in range(1, 11):
        add_attempt(gw, i, False)
    for i in range(11, 21):
        add_attempt(gw, i, True)
    # pre-landing review session for change 100 on a different job (not a sample)
    gw.add_session("pre", trigger_job="lustre-other")
    gw.add_test_set("prets", "pre", "SU", status="FAIL", submission="2026-07-11")
    gw.add_subtest("prets", "pre", "T2C", "FAIL")
    gw.link_review(100, "pre")
    patch_git(monkeypatch, [Commit("sha1", "2026-07-11", "A", "rsync fix", 100)],
              {"sha1": ["lustre/tests/lustre-rsync-test.sh"]})

    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21")
    assert data["analysis"]["classification"] == "regression"
    assert data["analysis"]["fails"] == 10
    assert data["suspects"][0]["change_number"] == 100
    assert "--change 100" in data["recommended_confirm"]


def test_bisect_flaky_stable_note_and_no_confirm_hint(gw, monkeypatch):
    for i in range(1, 21):
        add_attempt(gw, i, fail=(i % 2 == 1))
    patch_git(monkeypatch, [Commit("sha2", "2026-07-05", "B", "s", None)])
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21")
    assert data["analysis"]["classification"] == "flaky-stable"
    assert "no clear step-change" in data["note"]
    assert data["recommended_confirm"] is None


def test_bisect_no_job_no_config(gw):
    add_attempt(gw, 1, False)
    add_attempt(gw, 2, False)
    data = pipeline.run_bisect(gw, target(job=None), "2026-07-01", "2026-07-30")
    assert data["analysis"]["classification"] == "clean"
    assert data["warnings"] == []


def test_bisect_clean(gw):
    for i in range(1, 6):
        add_attempt(gw, i, False)
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21")
    assert data["analysis"]["classification"] == "clean"
    assert "not a regression" in data["note"]
    assert data["suspects"] == []


def test_bisect_insufficient(gw):
    add_attempt(gw, 1, True)
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21")
    assert data["analysis"]["classification"] == "insufficient"
    assert "insufficient" in data["note"]


def test_bisect_job_filter_excludes_other_jobs(gw):
    for i in range(1, 11):
        add_attempt(gw, i, False)
    for i in range(11, 21):
        add_attempt(gw, i, True)
    add_attempt(gw, 99, True, job="some-other-job")   # excluded by job filter
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-30")
    assert data["analysis"]["samples"] == 20


def test_bisect_config_filter(gw, monkeypatch):
    for i in range(1, 11):
        add_attempt(gw, i, False, fs="ldiskfs")
    for i in range(11, 21):
        add_attempt(gw, i, True, fs="ldiskfs")
    add_attempt(gw, 50, True, fs="zfs")               # filtered out by fs=ldiskfs
    patch_git(monkeypatch, [])
    data = pipeline.run_bisect(
        gw, target(), "2026-07-01", "2026-07-30",
        config_filter={"server_file_system": "ldiskfs"},
    )
    assert data["analysis"]["samples"] == 20


def test_bisect_config_without_job_warns(gw):
    add_attempt(gw, 1, False)
    add_attempt(gw, 2, False)
    data = pipeline.run_bisect(
        gw, target(job=None), "2026-07-01", "2026-07-30",
        config_filter={"server_file_system": "ldiskfs"},
    )
    assert any("--config ignored" in w for w in data["warnings"])


def test_bisect_job_session_cap_warning(gw):
    for i in range(1, 4):
        add_attempt(gw, i, False)
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-30", max_job_sessions=1)
    assert any("job session fetch hit" in w for w in data["warnings"])


def test_bisect_attempt_cap_warning(gw):
    for i in range(1, 4):
        add_attempt(gw, i, False)
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-30", max_attempts=1)
    assert any("suite-run fetch hit" in w for w in data["warnings"])


def test_bisect_max_change_sessions_warns(gw, monkeypatch):
    for i in range(1, 11):
        add_attempt(gw, i, False)
    for i in range(11, 21):
        add_attempt(gw, i, True)
    gw.add_session("pre", trigger_job="lustre-other")
    gw.add_test_set("prets", "pre", "SU", status="FAIL", submission="2026-07-11")
    gw.add_subtest("prets", "pre", "T2C", "FAIL")
    gw.link_review(100, "pre")
    patch_git(monkeypatch, [Commit("c1", "2026-07-11", "A", "x", 100)])
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21", max_change_sessions=1)
    assert any("at most 1 pre-landing" in w for w in data["warnings"])


def test_bisect_git_error_warns(gw, monkeypatch):
    for i in range(1, 11):
        add_attempt(gw, i, False)
    for i in range(11, 21):
        add_attempt(gw, i, True)
    monkeypatch.setattr(gitmap, "commits_in_window",
                        lambda *a, **k: (_ for _ in ()).throw(GitError("no clone")))
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21")
    assert any("git mapping failed" in w for w in data["warnings"])
    assert data["suspects"] == []


def test_bisect_max_suspects_cap(gw, monkeypatch):
    for i in range(1, 11):
        add_attempt(gw, i, False)
    for i in range(11, 21):
        add_attempt(gw, i, True)
    commits = [Commit(f"c{n}", "2026-07-11", "A", "x", None) for n in range(5)]
    patch_git(monkeypatch, commits)
    data = pipeline.run_bisect(gw, target(), "2026-07-01", "2026-07-21", max_suspects=2)
    assert len(data["suspects"]) == 2
    assert any("ranking the 2 newest" in w for w in data["warnings"])


# -- run_scan ----------------------------------------------------------

def _failed_session(gw, sid):
    gw.add_session(sid, trigger_job="lustre-b_es6_0", test_sets_failed_count=1)
    gw.add_test_set(f"{sid}-set", sid, "SU", status="FAIL")
    gw.add_test_set(f"{sid}-ok", sid, "OK", status="PASS")       # skipped (passing set)
    gw.add_subtest(f"{sid}-set", sid, "T2C", "FAIL", error="boom")
    gw.add_subtest(f"{sid}-set", sid, "TP", "PASS")               # skipped (passing subtest)


def test_scan_aggregates(gw):
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    _failed_session(gw, "a")
    _failed_session(gw, "b")
    data = pipeline.run_scan(gw, "lustre-b_es6_0", "2026-07-01", "2026-07-14")
    assert data["candidates"][0]["count"] == 2
    assert data["candidates"][0]["session_count"] == 2
    assert data["candidates"][0]["suite"] == "lustre-rsync-test"
    assert "patch_finder bisect --url" in data["candidates"][0]["bisect"]


def test_scan_cap_warning(gw):
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    _failed_session(gw, "a")
    _failed_session(gw, "b")
    data = pipeline.run_scan(gw, "lustre-b_es6_0", "2026-07-01", "2026-07-14", max_sessions=1)
    assert any("cap" in w for w in data["warnings"])


def test_scan_empty(gw):
    data = pipeline.run_scan(gw, "lustre-b_es6_0", "2026-07-01", "2026-07-14")
    assert data["candidates"] == []
