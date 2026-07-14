"""A minimal, date-bounded gateway to the Maloo REST API.

Only the read endpoints patch_finder needs, against the same ``/api/*`` surface
and HTTP Basic auth the installed ``maloo`` CLI uses.  The HTTP session is
injectable, so the gateway is fully unit-testable without a network.

Learned quirks baked in here:
  * date filtering must use ``from``/``to`` (the ``horizon`` param is ignored);
  * ``sub_tests`` must be queried WITH a ``status`` filter, else a common test
    times out — so :meth:`occurrences` queries one status at a time.
"""

from __future__ import annotations

from typing import Any, Sequence

import requests

from .config import MalooCredentials

FAIL_STATUSES: tuple[str, ...] = ("FAIL", "CRASH", "ABORT", "TIMEOUT")
PAGE_SIZE = 200


class MalooError(RuntimeError):
    """A Maloo API request failed (network, timeout, or HTTP error)."""


class MalooGateway:
    """Thin client over the Maloo ``/api`` endpoints."""

    def __init__(
        self,
        credentials: MalooCredentials,
        session: Any | None = None,
        timeout: int = 60,
    ) -> None:
        self._base = credentials.base_url
        self._timeout = timeout
        if session is None:  # pragma: no cover - exercised only against the live API
            session = requests.Session()
            session.auth = (credentials.username, credentials.password)
        self._session = session

    @property
    def base_url(self) -> str:
        return self._base

    def session_url(self, session_id: str) -> str:
        return f"{self._base}/test_sessions/{session_id}"

    # -- low-level --------------------------------------------------------
    def _get(self, endpoint: str, params: dict[str, Any]) -> list[dict]:
        try:
            resp = self._session.get(
                f"{self._base}/api/{endpoint}", params=params, timeout=self._timeout
            )
            resp.raise_for_status()
            body = resp.json()
        except requests.RequestException as exc:
            raise MalooError(f"Maloo API request to {endpoint!r} failed: {exc}") from exc
        return body if isinstance(body, list) else body.get("data", [])

    def _paginate(
        self, endpoint: str, params: dict[str, Any], max_records: int = 0
    ) -> list[dict]:
        params = dict(params)
        out: list[dict] = []
        offset = 0
        while not (max_records and len(out) >= max_records):
            params["offset"] = offset
            page = self._get(endpoint, params)
            out.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return out[:max_records] if max_records else out

    # -- single rows ------------------------------------------------------
    def session(self, session_id: str) -> dict | None:
        rows = self._get("test_sessions", {"id": session_id})
        return rows[0] if rows else None

    def test_set(self, test_set_id: str) -> dict | None:
        rows = self._get("test_sets", {"id": test_set_id})
        return rows[0] if rows else None

    def script_name(self, kind: str, script_id: str) -> str | None:
        """Resolve a ``{test_set,sub_test}_scripts`` id to its name."""
        rows = self._get(f"{kind}_scripts", {"id": script_id})
        return rows[0]["name"] if rows else None

    def script_ids(self, kind: str, name: str) -> list[str]:
        """All ``{test_set,sub_test}_scripts`` ids sharing ``name``.

        A subtest name like ``test_2c`` maps to many ids (one per suite), so
        callers must disambiguate against a real occurrence.
        """
        return [r["id"] for r in self._get(f"{kind}_scripts", {"name": name})]

    # -- collections ------------------------------------------------------
    def subtests_of_set(self, test_set_id: str) -> list[dict]:
        return self._paginate("sub_tests", {"test_set_id": test_set_id})

    def test_sets_of_session(self, session_id: str) -> list[dict]:
        return self._paginate("test_sets", {"test_session_id": session_id})

    def suite_runs(
        self, suite_script_id: str, from_date: str, to_date: str, max_records: int = 0
    ) -> list[dict]:
        """Every run (test_set) of one suite in a date window."""
        return self._paginate(
            "test_sets",
            {"test_set_script_id": suite_script_id, "from": from_date, "to": to_date},
            max_records,
        )

    def occurrences(
        self,
        sub_test_script_id: str,
        from_date: str,
        to_date: str,
        statuses: Sequence[str],
    ) -> list[dict]:
        """Every run of one specific subtest in a window, one status at a time."""
        out: list[dict] = []
        for status in statuses:
            out.extend(
                self._paginate(
                    "sub_tests",
                    {
                        "sub_test_script_id": sub_test_script_id,
                        "status": status,
                        "from": from_date,
                        "to": to_date,
                    },
                )
            )
        return out

    def sessions(
        self,
        trigger_job: str,
        from_date: str,
        to_date: str,
        failed_only: bool = False,
        max_records: int = 0,
    ) -> list[dict]:
        params: dict[str, Any] = {
            "trigger_job": trigger_job,
            "from": from_date,
            "to": to_date,
        }
        if failed_only:
            params["test_sets_failed"] = "true"
        return self._paginate("test_sessions", params, max_records)

    def review_sessions(
        self, review_id: int, patch: int | None = None, max_sessions: int = 0
    ) -> list[dict]:
        """The test sessions a Gerrit change was tested in, via ``code_reviews``.

        ``max_sessions`` (0 = unlimited) caps how many sessions are drilled — a
        change tested in many configs/patchsets is otherwise expensive to walk.
        """
        params: dict[str, Any] = {"review_id": review_id}
        if patch is not None:
            params["review_patch"] = patch
        seen: set[str] = set()
        ordered: list[str] = []
        for row in self._paginate("code_reviews", params):
            sid = row.get("test_session_id")
            if sid and sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        if max_sessions:
            ordered = ordered[:max_sessions]
        return [s for s in (self.session(sid) for sid in ordered) if s]
