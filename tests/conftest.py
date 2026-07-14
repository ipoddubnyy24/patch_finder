"""Shared in-memory fakes so the whole tool is testable without a network."""

from __future__ import annotations

import pytest

from patch_finder.config import MalooCredentials


class FakeGateway:
    """In-memory stand-in for MalooGateway with the same public surface."""

    def __init__(self, base_url: str = "https://maloo.test") -> None:
        self.base_url = base_url
        self.sessions_tbl: dict[str, dict] = {}
        self.test_sets_tbl: dict[str, dict] = {}
        self.subtests_tbl: list[dict] = []
        self.set_script_names: dict[str, str] = {}
        self.sub_script_names: dict[str, str] = {}
        self.code_reviews: dict[int, list[str]] = {}

    # -- builders -----------------------------------------------------
    def add_session(self, sid: str, **row) -> dict:
        self.sessions_tbl[sid] = {"id": sid, **row}
        return self.sessions_tbl[sid]

    def add_test_set(self, tsid: str, session_id: str, script_id: str, status: str = "PASS", **row) -> dict:
        self.test_sets_tbl[tsid] = {
            "id": tsid,
            "test_session_id": session_id,
            "test_set_script_id": script_id,
            "status": status,
            **row,
        }
        return self.test_sets_tbl[tsid]

    def add_subtest(self, test_set_id, session_id, script_id, status, error="", submission="2026-07-10") -> dict:
        row = {
            "test_set_id": test_set_id,
            "test_session_id": session_id,
            "sub_test_script_id": script_id,
            "status": status,
            "error": error,
            "submission": submission,
        }
        self.subtests_tbl.append(row)
        return row

    def name_set_script(self, sid: str, name: str) -> None:
        self.set_script_names[sid] = name

    def name_sub_script(self, sid: str, name: str) -> None:
        self.sub_script_names[sid] = name

    def link_review(self, review_id: int, session_id: str) -> None:
        self.code_reviews.setdefault(review_id, []).append(session_id)

    # -- gateway API --------------------------------------------------
    def session_url(self, sid: str) -> str:
        return f"{self.base_url}/test_sessions/{sid}"

    def session(self, sid):
        return self.sessions_tbl.get(sid)

    def test_set(self, tsid):
        return self.test_sets_tbl.get(tsid)

    def script_name(self, kind, sid):
        table = self.set_script_names if kind == "test_set" else self.sub_script_names
        return table.get(sid)

    def script_ids(self, kind, name):
        table = self.set_script_names if kind == "test_set" else self.sub_script_names
        return [sid for sid, n in table.items() if n == name]

    def subtests_of_set(self, tsid):
        return [s for s in self.subtests_tbl if s.get("test_set_id") == tsid]

    def test_sets_of_session(self, sid):
        return [ts for ts in self.test_sets_tbl.values() if ts.get("test_session_id") == sid]

    def suite_runs(self, suite_id, from_date, to_date, max_records=0):
        rows = [ts for ts in self.test_sets_tbl.values() if ts.get("test_set_script_id") == suite_id]
        return rows[:max_records] if max_records else rows

    def occurrences(self, sub_id, from_date, to_date, statuses):
        wanted = {s.upper() for s in statuses}
        return [
            s for s in self.subtests_tbl
            if s.get("sub_test_script_id") == sub_id
            and (s.get("status") or "").upper() in wanted
        ]

    def first_occurrence(self, sub_id, from_date, to_date,
                         statuses=("FAIL", "PASS", "SKIP", "CRASH", "TIMEOUT")):
        wanted = {s.upper() for s in statuses}
        for s in self.subtests_tbl:
            if s.get("sub_test_script_id") == sub_id and (s.get("status") or "").upper() in wanted:
                return s
        return None

    def sessions(self, job, from_date, to_date, failed_only=False, max_records=0):
        rows = [s for s in self.sessions_tbl.values() if s.get("trigger_job") == job]
        if failed_only:
            rows = [s for s in rows if s.get("test_sets_failed_count", 0) > 0]
        return rows[:max_records] if max_records else rows

    def review_sessions(self, review_id, patch=None, max_sessions=0):
        seen, ordered = set(), []
        for sid in self.code_reviews.get(review_id, []):
            if sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        if max_sessions:
            ordered = ordered[:max_sessions]
        return [s for s in (self.session(sid) for sid in ordered) if s]


@pytest.fixture
def gw():
    return FakeGateway()


@pytest.fixture
def creds():
    return MalooCredentials("https://maloo.test", "user", "pass")
