"""Orchestration: tie the gateway, timeline, git mapping and ranking together.

Everything is injected (gateway, git runner, clone path) so the whole pipeline
is unit-testable without a network or a real repo.
"""

from __future__ import annotations

from typing import Callable

from . import gitmap
from .maloo import FAIL_STATUSES, MalooGateway
from .resolve import Target
from .suspects import rank
from .timeline import detect, to_samples

# session fields a --config filter may constrain
CONFIG_FIELDS = {
    "fs": "server_file_system",
    "distro": "client_distribution",
    "arch": "client_architecture",
    "group": "test_group",
}

def parse_config_filter(spec: str) -> dict[str, str]:
    """Parse ``fs=ldiskfs,distro=RHEL 8.10`` into session-field constraints."""
    out: dict[str, str] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"bad --config token {part!r} (want key=value)")
        key, value = (p.strip() for p in part.split("=", 1))
        field = CONFIG_FIELDS.get(key)
        if not field:
            raise ValueError(f"unknown config key {key!r}; choose from {sorted(CONFIG_FIELDS)}")
        out[field] = value
    return out


def _suspect_dict(s) -> dict:
    return {
        "sha": s.commit.sha,
        "change_number": s.commit.change_number,
        "subject": s.commit.subject,
        "author": s.commit.author,
        "committed": s.commit.committed,
        "score": round(s.score, 3),
        "prelanding": s.prelanding,
        "reasons": s.reasons,
    }


def _confirm_hint(top, target) -> str | None:
    if top is None or top.commit.change_number is None:
        return None
    job = target.job or ""
    return (
        f"patch_finder confirm --change {top.commit.change_number} "
        f"--suite {target.suite} --test {target.test}"
        + (f" --job {job}" if job else "")
        + " --bug LU-XXXXX --runs 20 --execute"
    )


def run_bisect(
    gw: MalooGateway,
    target: Target,
    from_date: str,
    to_date: str,
    *,
    git_run: gitmap.Runner = gitmap.default_runner,
    clone: str = "",
    config_filter: dict[str, str] | None = None,
    max_suspects: int = 30,
    max_attempts: int = 3000,
    max_job_sessions: int = 5000,
) -> dict:
    warnings: list[str] = []
    # Failures are rare and fast to fetch; total attempts come from the suite's
    # runs.  (Querying every PASS of a common subtest is infeasible on Maloo, so
    # a run is counted as failing iff this subtest failed in it, else passing.)
    fail_set = {
        r["test_set_id"]
        for r in gw.occurrences(target.sub_test_script_id, from_date, to_date, FAIL_STATUSES)
        if r.get("test_set_id")
    }
    runs = gw.suite_runs(target.suite_script_id, from_date, to_date, max_records=max_attempts)
    if len(runs) >= max_attempts:
        warnings.append(f"suite-run fetch hit the {max_attempts} cap; history may be truncated")

    if target.job:
        job_sessions = gw.sessions(target.job, from_date, to_date, max_records=max_job_sessions)
        if len(job_sessions) >= max_job_sessions:
            warnings.append(
                f"job session fetch hit the {max_job_sessions} cap; history may be truncated"
            )
        wanted = {
            s["id"] for s in job_sessions
            if all(s.get(f) == v for f, v in (config_filter or {}).items())
        }
        runs = [r for r in runs if r.get("test_session_id") in wanted]
    elif config_filter:
        warnings.append("--config ignored: no --job given to resolve session configs")

    samples = to_samples(
        [{**r, "status": "FAIL" if r["id"] in fail_set else "PASS"} for r in runs]
    )
    cp = detect(samples)

    suspects: list = []
    note = ""
    # regression/flaky-stable always carry a transition window; clean/insufficient don't.
    if cp.classification in ("regression", "flaky-stable"):
        try:
            commits = gitmap.commits_in_window(
                git_run, clone, target.base_branch, cp.last_good_ts, cp.first_bad_ts
            )
        except gitmap.GitError as exc:
            commits = []
            warnings.append(f"git mapping failed: {exc}")
        if len(commits) > max_suspects:
            warnings.append(
                f"{len(commits)} commits in window; ranking the {max_suspects} newest"
            )
            commits = commits[:max_suspects]
        suspects = rank(
            gw, commits, target, lambda sha: gitmap.files_of(git_run, clone, sha)
        )
        if cp.classification == "flaky-stable":
            note = (
                "no clear step-change: persistent flakiness or sparse data. "
                "Treat suspects as weak leads; use `confirm` to sample the true fail-rate."
            )
    elif cp.classification == "clean":
        note = "no failures in the window — not a regression, or wrong target/window."
    else:
        note = "insufficient data in the window; widen --days or check the target."

    top = suspects[0] if suspects else None
    fails = sum(1 for s in samples if s.fail)
    return {
        "target": {
            "suite": target.suite,
            "test": target.test,
            "job": target.job,
            "base_branch": target.base_branch,
            "config": target.config,
            "error_sample": target.error_sample,
        },
        "window": {"from": from_date, "to": to_date},
        "analysis": {
            "classification": cp.classification,
            "samples": len(samples),
            "fails": fails,
            "before_rate": round(cp.before_rate, 4),
            "after_rate": round(cp.after_rate, 4),
            "delta": round(cp.delta, 4),
            "lr": round(cp.lr, 3),
            "p_value": round(cp.p_value, 4),
            "n_before": cp.n_before,
            "n_after": cp.n_after,
            "last_good": cp.last_good_ts,
            "first_bad": cp.first_bad_ts,
        },
        "suspects": [_suspect_dict(s) for s in suspects],
        "note": note,
        "recommended_confirm": _confirm_hint(top, target),
        "warnings": warnings,
    }


def run_scan(
    gw: MalooGateway,
    job: str,
    from_date: str,
    to_date: str,
    *,
    top: int = 20,
    max_sessions: int = 400,
) -> dict:
    warnings: list[str] = []
    sessions = gw.sessions(job, from_date, to_date, failed_only=True, max_records=max_sessions)
    if len(sessions) >= max_sessions:
        warnings.append(f"hit the {max_sessions}-session cap; increase --max-sessions for full coverage")

    set_names: dict[str, str] = {}
    sub_names: dict[str, str] = {}
    agg: dict[tuple[str, str], dict] = {}

    def set_name(sid: str) -> str:
        if sid not in set_names:
            set_names[sid] = gw.script_name("test_set", sid) or "unknown"
        return set_names[sid]

    def sub_name(sid: str) -> str:
        if sid not in sub_names:
            sub_names[sid] = gw.script_name("sub_test", sid) or "unknown"
        return sub_names[sid]

    for sess in sessions:
        for ts in gw.test_sets_of_session(sess["id"]):
            if (ts.get("status") or "").upper() not in FAIL_STATUSES:
                continue
            suite = set_name(ts.get("test_set_script_id", ""))
            for st in gw.subtests_of_set(ts["id"]):
                if (st.get("status") or "").upper() not in FAIL_STATUSES:
                    continue
                test = sub_name(st.get("sub_test_script_id", ""))
                key = (suite, test)
                entry = agg.setdefault(
                    key,
                    {
                        "suite": suite,
                        "test": test,
                        "count": 0,
                        "sessions": set(),
                        "error_sample": st.get("error", "") or "",
                        "example_test_set_id": ts["id"],
                    },
                )
                entry["count"] += 1
                entry["sessions"].add(sess["id"])

    ranked = sorted(agg.values(), key=lambda e: -e["count"])[:top]
    for e in ranked:
        e["session_count"] = len(e.pop("sessions"))
        e["bisect"] = (
            f"patch_finder bisect --url {gw.base_url}/test_sets/{e['example_test_set_id']}"
        )
    return {
        "job": job,
        "window": {"from": from_date, "to": to_date},
        "sessions_examined": len(sessions),
        "candidates": ranked,
        "warnings": warnings,
    }
