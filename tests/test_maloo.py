import pytest
import requests

from patch_finder.config import MalooCredentials
from patch_finder.maloo import PAGE_SIZE, MalooError, MalooGateway


class FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self._status = status

    def raise_for_status(self):
        if self._status >= 400:
            raise requests.HTTPError(str(self._status))

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        payload, status = self.handler(url, dict(params or {}))
        return FakeResp(payload, status)


def gwith(handler):
    http = FakeHttp(handler)
    gw = MalooGateway(MalooCredentials("https://maloo.test", "u", "p"), session=http)
    return gw, http


def test_urls():
    gw, _ = gwith(lambda u, p: ([], 200))
    assert gw.base_url == "https://maloo.test"
    assert gw.session_url("abc") == "https://maloo.test/test_sessions/abc"


def test_get_handles_list_and_dict_bodies():
    gw, _ = gwith(lambda u, p: ({"data": [{"id": "s1"}]}, 200))
    assert gw.session("s1") == {"id": "s1"}
    gw2, _ = gwith(lambda u, p: ([{"id": "t1"}], 200))
    assert gw2.test_set("t1") == {"id": "t1"}


def test_single_row_missing_returns_none():
    gw, _ = gwith(lambda u, p: ([], 200))
    assert gw.session("x") is None
    assert gw.test_set("x") is None
    assert gw.script_name("sub_test", "x") is None


def test_script_name_and_ids():
    def h(url, params):
        if url.endswith("/sub_test_scripts"):
            if params.get("id"):
                return ([{"id": "sid", "name": "test_2c"}], 200)
            return ([{"id": "a"}, {"id": "b"}], 200)
        return ([], 200)  # pragma: no cover

    gw, _ = gwith(h)
    assert gw.script_name("sub_test", "sid") == "test_2c"
    assert gw.script_ids("sub_test", "test_2c") == ["a", "b"]


def test_http_error_becomes_maloo_error():
    gw, _ = gwith(lambda u, p: (None, 500))
    with pytest.raises(MalooError):
        gw.session("x")


def test_network_error_becomes_maloo_error():
    class BoomHttp:
        def get(self, *a, **k):
            raise requests.ConnectTimeout("boom")

    gw = MalooGateway(MalooCredentials("https://maloo.test", "u", "p"), session=BoomHttp())
    with pytest.raises(MalooError, match="failed"):
        gw.session("x")


def test_pagination_walks_offsets():
    def h(url, params):
        return ([{"id": i} for i in range(PAGE_SIZE if params.get("offset", 0) == 0 else 50)], 200)

    gw, http = gwith(h)
    rows = gw.subtests_of_set("ts1")
    assert len(rows) == PAGE_SIZE + 50
    assert len(http.calls) == 2
    assert http.calls[1][1]["offset"] == PAGE_SIZE


def test_pagination_respects_max_records():
    gw, http = gwith(lambda u, p: ([{"id": i} for i in range(PAGE_SIZE)], 200))
    rows = gw.suite_runs("suite", "2026-07-01", "2026-07-14", max_records=10)
    assert len(rows) == 10
    assert http.calls[0][1]["from"] == "2026-07-01" and http.calls[0][1]["to"] == "2026-07-14"


def test_occurrences_queries_each_status():
    gw, http = gwith(lambda u, p: ([{"status": p.get("status")}], 200))
    rows = gw.occurrences("sub", "2026-07-01", "2026-07-14", ("PASS", "FAIL"))
    assert {r["status"] for r in rows} == {"PASS", "FAIL"}
    assert [c[1]["status"] for c in http.calls] == ["PASS", "FAIL"]


def test_sessions_failed_only_flag():
    gw, http = gwith(lambda u, p: ([], 200))
    gw.sessions("lustre-master", "2026-07-01", "2026-07-14")
    assert "test_sets_failed" not in http.calls[0][1]
    gw.sessions("lustre-master", "2026-07-01", "2026-07-14", failed_only=True)
    assert http.calls[1][1]["test_sets_failed"] == "true"


def test_test_sets_of_session():
    gw, _ = gwith(lambda u, p: ([{"id": "ts1"}], 200))
    assert gw.test_sets_of_session("s1") == [{"id": "ts1"}]


def test_review_sessions_dedupes_and_drops_missing():
    def h(url, params):
        if url.endswith("/code_reviews"):
            if params.get("offset", 0):
                return ([], 200)
            return ([
                {"test_session_id": "s1"},
                {"test_session_id": "s1"},   # duplicate
                {"test_session_id": "s2"},   # session missing below
                {"other": 1},                # no session id
            ], 200)
        if url.endswith("/test_sessions"):
            return ([{"id": "s1"}], 200) if params.get("id") == "s1" else ([], 200)
        return ([], 200)  # pragma: no cover

    gw, _ = gwith(h)
    sessions = gw.review_sessions(65428)
    assert sessions == [{"id": "s1"}]


def test_first_occurrence_tries_statuses_in_order():
    def h(url, params):
        if params.get("status") == "FAIL":
            return ([], 200)                                  # nothing failing
        return ([{"id": "x", "test_set_id": "ts1"}], 200)     # PASS has a row

    gw, http = gwith(h)
    row = gw.first_occurrence("sub", "2026-07-01", "2026-07-14")
    assert row["test_set_id"] == "ts1"
    assert [c[1]["status"] for c in http.calls[:2]] == ["FAIL", "PASS"]


def test_first_occurrence_none_when_absent():
    gw, _ = gwith(lambda u, p: ([], 200))
    assert gw.first_occurrence("sub", "2026-07-01", "2026-07-14") is None


def test_review_sessions_with_patch_filter():
    gw, http = gwith(lambda u, p: ([], 200))
    gw.review_sessions(5, patch=2)
    assert http.calls[0][1]["review_patch"] == 2


def test_review_sessions_max_sessions_cap():
    def h(url, params):
        if url.endswith("/code_reviews"):
            if params.get("offset", 0):
                return ([], 200)
            return ([{"test_session_id": s} for s in ("s1", "s2", "s3")], 200)
        return ([{"id": params.get("id")}], 200)

    gw, _ = gwith(h)
    sessions = gw.review_sessions(5, max_sessions=2)
    assert [s["id"] for s in sessions] == ["s1", "s2"]


def test_review_sessions_stops_paginating_at_cap():
    # A full first page must not stop early; the cap is only reached on page 2.
    def h(url, params):
        if url.endswith("/code_reviews"):
            off = params.get("offset", 0)
            if off == 0:
                return ([{"test_session_id": f"s{i}"} for i in range(PAGE_SIZE)], 200)
            return ([{"test_session_id": f"s{PAGE_SIZE + i}"} for i in range(5)], 200)
        return ([{"id": params.get("id")}], 200)

    gw, http = gwith(h)
    sessions = gw.review_sessions(9, max_sessions=PAGE_SIZE + 1)
    assert len(sessions) == PAGE_SIZE + 1
    # exactly two code_reviews pages fetched, then it stopped
    assert sum(1 for url, _ in http.calls if url.endswith("/code_reviews")) == 2
