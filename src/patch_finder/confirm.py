"""Stage 5 — confirm a flaky suspect by re-running its pre-landing session(s).

We reproduce the failure statistically by requeuing a suspect change's existing
Maloo sessions K times via the installed ``maloo retest`` CLI, then re-reading
the fail-rate.  This is the least-invasive active option: it reuses
already-approved sessions rather than pushing new Gerrit patchsets.

Safety: planning/executing a retest requires a justification ticket, and
nothing is fired unless the caller passes an explicit execute flag.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Callable

from .maloo import MalooGateway
from .resolve import Target
from .suspects import prelanding_verdict

RetestRunner = Callable[[list[str]], dict]


class ConfirmError(RuntimeError):
    pass


@dataclass
class RetestAction:
    session_id: str
    session_url: str
    command: list[str]


def default_retest_runner(command: list[str], timeout: int = 120) -> dict:  # pragma: no cover - shells out to the live CLI
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def _sessions_running_target(gw: MalooGateway, change_number: int, target: Target) -> list[str]:
    out: list[str] = []
    for sess in gw.review_sessions(change_number):
        for ts in gw.test_sets_of_session(sess["id"]):
            if ts.get("test_set_script_id") != target.suite_script_id:
                continue
            if any(
                st.get("sub_test_script_id") == target.sub_test_script_id
                for st in gw.subtests_of_set(ts["id"])
            ):
                out.append(sess["id"])
                break
    return out


def plan(
    gw: MalooGateway, change_number: int, target: Target, bug: str, runs: int
) -> list[RetestAction]:
    """Build the (dry-run) list of ``maloo retest`` invocations."""
    if not bug:
        raise ConfirmError("a justification ticket (--bug LU-xxxxx) is required")
    if runs < 1:
        raise ConfirmError("--runs must be >= 1")
    actions: list[RetestAction] = []
    for sid in _sessions_running_target(gw, change_number, target):
        url = gw.session_url(sid)
        for _ in range(runs):
            actions.append(
                RetestAction(sid, url, ["maloo", "retest", url, bug, "--option", "single"])
            )
    return actions


def execute(actions: list[RetestAction], runner: RetestRunner = default_retest_runner) -> list[dict]:
    """Fire the planned retests, returning one result dict per action."""
    return [{"session_id": a.session_id, **runner(a.command)} for a in actions]


def collect(gw: MalooGateway, change_number: int, target: Target) -> dict:
    """Re-read the failing test's current pass/fail tally for this change."""
    return prelanding_verdict(
        gw, change_number, target.suite_script_id, target.sub_test_script_id
    )
