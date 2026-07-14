from patch_finder import report


def _bisect_data(**over):
    data = {
        "target": {
            "suite": "lustre-rsync-test", "test": "test_2c", "job": "lustre-reviews",
            "base_branch": "master", "config": {"server_fs": "ldiskfs", "arch": None},
            "error_sample": "Failure in replication",
        },
        "window": {"from": "2026-07-01", "to": "2026-07-14"},
        "analysis": {
            "classification": "regression", "samples": 20, "fails": 10,
            "before_rate": 0.0, "after_rate": 1.0, "delta": 1.0, "lr": 27.7,
            "p_value": 0.004, "n_before": 10, "n_after": 10,
            "last_good": "2026-07-08", "first_bad": "2026-07-09",
        },
        "suspects": [
            {"sha": "abc123def456", "change_number": 100, "subject": "rsync fix",
             "author": "A", "committed": "2026-07-09", "score": 11.0,
             "prelanding": {}, "reasons": ["pre-landing already FAILED 1/1x", "touches area"]},
        ],
        "note": "",
        "recommended_confirm": "patch_finder confirm --change 100 ...",
        "warnings": ["job session fetch hit the cap"],
    }
    data.update(over)
    return data


def test_render_bisect_full():
    text = report.render_bisect(_bisect_data())
    assert "target: lustre-rsync-test test_2c" in text
    assert "config: server_fs=ldiskfs" in text     # None arch dropped
    assert "error :" in text
    assert "verdict: REGRESSION" in text
    assert "transition: last-good 2026-07-08" in text
    assert "1. [+11.0] abc123def456 change 100" in text
    assert "- pre-landing already FAILED 1/1x" in text
    assert "warning: job session fetch hit the cap" in text
    assert "confirm the top suspect:" in text


def test_render_bisect_minimal():
    data = _bisect_data(
        target={"suite": "sanity", "test": "test_1", "job": None, "base_branch": "master",
                "config": {}, "error_sample": ""},
        analysis={**_bisect_data()["analysis"], "last_good": None, "classification": "clean"},
        suspects=[],
        note="no failures in the window.",
        recommended_confirm=None,
        warnings=[],
    )
    text = report.render_bisect(data)
    assert "config:" not in text
    assert "error :" not in text
    assert "transition:" not in text
    assert "suspects" not in text
    assert "note: no failures" in text
    assert "confirm the top suspect" not in text


def test_render_bisect_suspect_without_change():
    data = _bisect_data(suspects=[
        {"sha": "aaaaaaaaaaaa", "change_number": None, "subject": "x", "author": "A",
         "committed": "2026-07-09", "score": 0.0, "prelanding": {}, "reasons": []}
    ])
    assert "no-gerrit-link" in report.render_bisect(data)


def test_render_scan():
    data = {
        "job": "lustre-b_es6_0", "window": {"from": "2026-07-04", "to": "2026-07-14"},
        "sessions_examined": 12,
        "candidates": [
            {"suite": "lustre-rsync-test", "test": "test_2c", "count": 5,
             "session_count": 4, "error_sample": "boom",
             "bisect": "patch_finder bisect --url https://x/test_sets/z"},
            {"suite": "sanity", "test": "test_9", "count": 2, "session_count": 2,
             "error_sample": "", "bisect": "patch_finder bisect --url https://x/test_sets/y"},
        ],
        "warnings": ["hit the cap"],
    }
    text = report.render_scan(data)
    assert "1. lustre-rsync-test test_2c   x5 in 4 sessions" in text
    assert "boom" in text
    assert "warning: hit the cap" in text


def test_render_scan_empty():
    text = report.render_scan({
        "job": "j", "window": {"from": "a", "to": "b"}, "sessions_examined": 0,
        "candidates": [], "warnings": [],
    })
    assert "no failures found" in text


def test_render_confirm_dry_run_empty():
    text = report.render_confirm({
        "change": 100, "target": "lustre-rsync-test test_2c", "mode": "dry-run",
        "actions": [], "warnings": [],
    })
    assert "nothing to retest" in text


def test_render_confirm_dry_run_actions():
    text = report.render_confirm({
        "change": 100, "target": "t", "mode": "dry-run",
        "actions": [{"command": ["maloo", "retest", "u", "LU-1", "--option", "single"], "session_id": "s"}],
        "warnings": ["placeholder"],
    })
    assert "would fire 1 retest" in text
    assert "maloo retest u LU-1 --option single" in text
    assert "warning: placeholder" in text


def test_render_confirm_execute():
    text = report.render_confirm({
        "change": 100, "target": "t", "mode": "execute",
        "results": [
            {"session_id": "s1", "returncode": 0},
            {"session_id": "s2", "returncode": 3},
        ],
        "warnings": [],
    })
    assert "s1: ok" in text
    assert "s2: FAILED rc=3" in text


def test_render_confirm_collect():
    assert "1/2 failing (50.0%)" in report.render_confirm({
        "change": 100, "target": "t", "mode": "collect",
        "verdict": {"fail": 1, "total": 2}, "warnings": [],
    })
    assert "0/0 failing (0.0%)" in report.render_confirm({
        "change": 100, "target": "t", "mode": "collect",
        "verdict": {"fail": 0, "total": 0}, "warnings": [],
    })


def test_envelope():
    env = report.envelope({"x": 1}, "bisect")
    assert env == {"ok": True, "data": {"x": 1}, "meta": {"tool": "patch_finder", "command": "bisect"}}
    assert report.envelope(None, "scan", ok=False)["ok"] is False
