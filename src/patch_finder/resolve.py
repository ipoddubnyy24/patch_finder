"""Turn a user reference into a resolved :class:`Target`.

A reference is either a Maloo URL (``/test_sets/<uuid>`` or
``/test_sessions/<uuid>``) or an explicit ``suite`` + ``test`` (+ ``job``).

The (suite, subtest) script-ids are always pinned from a *real occurrence*,
never from the subtest name alone — because one name (e.g. ``test_2c``) maps
to many script-ids, one per suite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .maloo import FAIL_STATUSES, MalooGateway

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_URL_RE = re.compile(rf"/(test_sessions|test_sets)/({_UUID})", re.I)
_UUID_RE = re.compile(_UUID, re.I)


class ResolveError(RuntimeError):
    pass


@dataclass
class Target:
    suite: str
    suite_script_id: str
    test: str
    sub_test_script_id: str
    job: str | None
    base_branch: str
    error_sample: str = ""
    config: dict = field(default_factory=dict)


def parse_ref(text: str) -> tuple[str | None, str]:
    """Return ``(kind, uuid)``; ``kind`` is None for a bare UUID."""
    m = _URL_RE.search(text)
    if m:
        return m.group(1), m.group(2).lower()
    m = _UUID_RE.search(text)
    if m:
        return None, m.group(0).lower()
    raise ResolveError(f"no Maloo id found in: {text!r}")


def _config_of(sess: dict) -> dict:
    return {
        "trigger_job": sess.get("trigger_job"),
        "test_group": sess.get("test_group"),
        "server_fs": sess.get("server_file_system"),
        "client_distro": sess.get("client_distribution"),
        "arch": sess.get("client_architecture"),
    }


def _base_branch_of(sess: dict) -> str:
    return sess.get("server_branch") or sess.get("client_branch") or "master"


def job_to_base_branch(job: str | None) -> str:
    """Best-effort base branch for a job when no session is available."""
    if not job:
        return "master"
    if job in ("lustre-reviews", "lustre-master"):
        return "master"
    stripped = job[len("lustre-"):] if job.startswith("lustre-") else job
    return "master" if stripped.endswith("reviews") else stripped


def _pin_subtest_in_set(gw: MalooGateway, test_set_id: str, candidate_ids: set[str]) -> str | None:
    for st in gw.subtests_of_set(test_set_id):
        sid = st.get("sub_test_script_id")
        if sid and sid in candidate_ids:
            return sid
    return None


def _pin_from_recent(
    gw: MalooGateway, suite_script_id: str, test: str, from_date: str, to_date: str, scan: int = 25
) -> str:
    candidates = set(gw.script_ids("sub_test", test))
    if not candidates:
        raise ResolveError(f"no subtest named {test!r} known to Maloo")
    for run in gw.suite_runs(suite_script_id, from_date, to_date, max_records=scan):
        sid = _pin_subtest_in_set(gw, run["id"], candidates)
        if sid:
            return sid
    raise ResolveError(
        f"could not find {test!r} in any recent run of that suite; widen --days"
    )


def _resolve_test_set(gw: MalooGateway, uuid: str, test: str | None, job: str | None) -> Target:
    ts = gw.test_set(uuid)
    if not ts:
        raise ResolveError(f"test_set {uuid} not found")
    suite_script_id = ts["test_set_script_id"]
    suite = gw.script_name("test_set", suite_script_id) or "unknown"
    sess = gw.session(ts.get("test_session_id")) or {}
    subs = gw.subtests_of_set(uuid)

    def name_of(sid: str | None) -> str | None:
        return gw.script_name("sub_test", sid) if sid else None

    if test:
        candidates = set(gw.script_ids("sub_test", test))
        chosen = next(
            (s for s in subs if s.get("sub_test_script_id") in candidates), None
        )
        if not chosen:
            raise ResolveError(f"{test!r} not found in test_set {uuid}")
    else:
        failing = [s for s in subs if (s.get("status") or "").upper() in FAIL_STATUSES]
        if len(failing) == 1:
            chosen = failing[0]
        elif not failing:
            raise ResolveError("no failing subtest in this test_set; pass --test")
        else:
            names = sorted({name_of(s.get("sub_test_script_id")) or "?" for s in failing})
            raise ResolveError("multiple failing subtests; pass --test one of: " + ", ".join(names))

    return Target(
        suite=suite,
        suite_script_id=suite_script_id,
        test=test or name_of(chosen.get("sub_test_script_id")) or "unknown",
        sub_test_script_id=chosen["sub_test_script_id"],
        job=job or sess.get("trigger_job"),
        base_branch=_base_branch_of(sess),
        error_sample=chosen.get("error", "") or "",
        config=_config_of(sess),
    )


def _resolve_session(
    gw: MalooGateway, uuid: str, suite: str | None, test: str | None, job: str | None,
    from_date: str, to_date: str,
) -> Target:
    sess = gw.session(uuid)
    if not sess:
        raise ResolveError(f"test_session {uuid} not found")
    if not (suite and test):
        raise ResolveError("a session URL needs --suite and --test")
    suite_ids = gw.script_ids("test_set", suite)
    if not suite_ids:
        raise ResolveError(f"unknown suite {suite!r}")
    suite_script_id = suite_ids[0]
    candidates = set(gw.script_ids("sub_test", test))
    sub_id = None
    for ts in gw.test_sets_of_session(uuid):
        if ts.get("test_set_script_id") == suite_script_id:
            sub_id = _pin_subtest_in_set(gw, ts["id"], candidates)
            if sub_id:
                break
    if not sub_id:
        sub_id = _pin_from_recent(gw, suite_script_id, test, from_date, to_date)
    return Target(
        suite=suite,
        suite_script_id=suite_script_id,
        test=test,
        sub_test_script_id=sub_id,
        job=job or sess.get("trigger_job"),
        base_branch=_base_branch_of(sess),
        config=_config_of(sess),
    )


def resolve_target(
    gw: MalooGateway,
    *,
    url: str | None = None,
    suite: str | None = None,
    test: str | None = None,
    job: str | None = None,
    base_branch: str | None = None,
    from_date: str = "",
    to_date: str = "",
) -> Target:
    """Resolve a bisect target from a URL or an explicit suite/test/job."""
    if url:
        kind, uuid = parse_ref(url)
        if kind == "test_sets":
            target = _resolve_test_set(gw, uuid, test, job)
        elif kind == "test_sessions":
            target = _resolve_session(gw, uuid, suite, test, job, from_date, to_date)
        elif gw.test_set(uuid):
            # Bare UUID: try test_set first, then session.
            target = _resolve_test_set(gw, uuid, test, job)
        else:
            target = _resolve_session(gw, uuid, suite, test, job, from_date, to_date)
    else:
        if not (suite and test):
            raise ResolveError("without a URL you must pass --suite and --test")
        suite_ids = gw.script_ids("test_set", suite)
        if not suite_ids:
            raise ResolveError(f"unknown suite {suite!r}")
        suite_script_id = suite_ids[0]
        sub_id = _pin_from_recent(gw, suite_script_id, test, from_date, to_date)
        target = Target(
            suite=suite,
            suite_script_id=suite_script_id,
            test=test,
            sub_test_script_id=sub_id,
            job=job,
            base_branch=job_to_base_branch(job),
        )

    if base_branch:
        target.base_branch = base_branch
    return target
