"""Map a time window to the Lustre commits that landed in it, using a local
git clone, and recover each commit's Gerrit change number from its
``Reviewed-on:`` trailer.

Subprocess I/O (:func:`default_runner`) is separated from parsing so the
parsing is fully unit-testable with canned ``git`` output.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

# Record/field separators unlikely to appear in a commit message.
_REC = "\x1e"
_FS = "\x1f"
_REVIEW_RE = re.compile(r"Reviewed-on:\s*https?://\S+/\+/(\d+)")

Runner = Callable[..., str]


class GitError(RuntimeError):
    pass


@dataclass
class Commit:
    sha: str
    committed: str
    author: str
    subject: str
    change_number: int | None


def default_runner(args: list[str], cwd: str, timeout: int = 60) -> str:
    """Run ``git -C <cwd> <args>`` and return stdout, raising on failure."""
    proc = subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def parse_log(output: str) -> list[Commit]:
    """Parse the sentinel-delimited output of :func:`commits_in_window`."""
    commits: list[Commit] = []
    for rec in output.split(_REC):
        if not rec.strip():
            continue
        parts = rec.split(_FS)
        if len(parts) < 5:
            continue
        sha, committed, author, subject, body = parts[:5]
        m = _REVIEW_RE.search(body)
        commits.append(
            Commit(
                sha=sha.strip(),
                committed=committed.strip(),
                author=author.strip(),
                subject=subject.strip(),
                change_number=int(m.group(1)) if m else None,
            )
        )
    return commits


def pick_ref(run: Runner, clone: str, branch: str) -> str:
    """Prefer ``origin/<branch>`` (freshest), fall back to a local ``<branch>``."""
    for ref in (f"origin/{branch}", branch):
        try:
            run(["rev-parse", "--verify", "--quiet", ref], clone)
            return ref
        except GitError:
            continue
    raise GitError(f"branch not found in clone: {branch}")


def commits_in_window(
    run: Runner, clone: str, branch: str, since_iso: str, until_iso: str
) -> list[Commit]:
    """Commits on ``branch`` with a committer date in (since, until]."""
    ref = pick_ref(run, clone, branch)
    fmt = f"%H{_FS}%cI{_FS}%an{_FS}%s{_FS}%b{_REC}"
    out = run(
        [
            "log",
            f"--since={since_iso}",
            f"--until={until_iso}",
            "--date=iso-strict",
            f"--pretty=format:{fmt}",
            ref,
        ],
        clone,
    )
    return parse_log(out)


def files_of(run: Runner, clone: str, sha: str) -> list[str]:
    """The paths a single commit changed."""
    out = run(["show", "--name-only", "--pretty=format:", sha], clone)
    return [line.strip() for line in out.splitlines() if line.strip()]


def fetch(run: Runner, clone: str, branch: str) -> None:  # pragma: no cover - thin I/O
    """Best-effort refresh of ``origin/<branch>`` (used only with --fetch)."""
    run(["fetch", "--quiet", "origin", branch], clone)
