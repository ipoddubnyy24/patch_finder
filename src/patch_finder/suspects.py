"""Rank the landed commits in the transition window by likelihood of being the
culprit.

The strong signal is each commit's *own* pre-landing Maloo verdict for the
failing (suite, subtest): a patch that already failed this exact test in its
review testing is the obvious suspect.  A light diff-relevance score (does the
patch touch the test's area / share keywords with the failure) breaks ties.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .gitmap import Commit
from .maloo import FAIL_STATUSES, MalooGateway
from .resolve import Target

FilesGetter = Callable[[str], list[str]]

# Scoring weights (documented, not magic): a pre-landing failure dominates.
_W_PRELAND_FAIL = 10.0
_W_PRELAND_PASS = -2.0


@dataclass
class Suspect:
    commit: Commit
    prelanding: dict
    relevance: float
    score: float
    reasons: list[str] = field(default_factory=list)


def prelanding_verdict(
    gw: MalooGateway,
    change_number: int | None,
    suite_script_id: str,
    sub_test_script_id: str,
    max_sessions: int = 0,
) -> dict:
    """How the failing test fared in this change's own pre-landing sessions.

    ``max_sessions`` (0 = unlimited) bounds how many of the change's sessions
    are drilled.
    """
    if change_number is None:
        return {"tested": False, "failed": False, "fail": 0, "total": 0}
    fail = total = 0
    for sess in gw.review_sessions(change_number, max_sessions=max_sessions):
        for ts in gw.test_sets_of_session(sess["id"]):
            if ts.get("test_set_script_id") != suite_script_id:
                continue
            for st in gw.subtests_of_set(ts["id"]):
                if st.get("sub_test_script_id") != sub_test_script_id:
                    continue
                total += 1
                if (st.get("status") or "").upper() in FAIL_STATUSES:
                    fail += 1
    return {"tested": total > 0, "failed": fail > 0, "fail": fail, "total": total}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z_]{4,}", text.lower()))


def diff_relevance(
    files: list[str], subject: str, suite: str, error_sample: str
) -> tuple[float, list[str]]:
    score = 0.0
    reasons: list[str] = []
    suite_l = suite.lower()
    if any(suite_l in f.lower() for f in files):
        score += 1.0
        reasons.append(f"touches {suite} area")
    overlap = _tokens(subject + " " + error_sample) & _tokens(" ".join(files))
    overlap.discard(suite_l)
    if overlap:
        score += min(1.0, 0.25 * len(overlap))
        reasons.append("keyword overlap: " + ", ".join(sorted(overlap))[:60])
    return score, reasons


def rank(
    gw: MalooGateway,
    commits: list[Commit],
    target: Target,
    files_getter: FilesGetter,
    max_sessions: int = 0,
) -> list[Suspect]:
    suspects: list[Suspect] = []
    for c in commits:
        verdict = prelanding_verdict(
            gw, c.change_number, target.suite_script_id, target.sub_test_script_id,
            max_sessions=max_sessions,
        )
        files = files_getter(c.sha) if c.change_number is not None else []
        relevance, reasons = diff_relevance(files, c.subject, target.suite, target.error_sample)
        score = relevance
        if verdict["failed"]:
            score += _W_PRELAND_FAIL
            reasons.insert(0, f"pre-landing already FAILED {verdict['fail']}/{verdict['total']}x")
        elif verdict["tested"]:
            score += _W_PRELAND_PASS
            reasons.insert(0, f"pre-landing PASSED {verdict['total']}x")
        suspects.append(Suspect(c, verdict, relevance, score, reasons))
    suspects.sort(key=lambda s: (-s.score, s.commit.committed))
    return suspects
