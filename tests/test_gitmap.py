import subprocess

import pytest

from patch_finder.gitmap import (
    _FS,
    _REC,
    GitError,
    commits_in_window,
    default_runner,
    files_of,
    parse_log,
    pick_ref,
)


def _rec(sha, committed, author, subject, body):
    return _FS.join([sha, committed, author, subject, body]) + _REC


def test_parse_log_extracts_change_number_and_skips_bad_records():
    out = (
        _rec("aaa", "2026-07-10T00:00:00+00:00", "Al", "fix rsync",
             "body\nReviewed-on: https://review.whamcloud.com/c/ex/lustre-release/+/65428\n")
        + _rec("bbb", "2026-07-11T00:00:00+00:00", "Bo", "no trailer", "just a body")
        + "shortrecord" + _REC        # too few fields -> skipped
        + "   " + _REC                 # blank -> skipped
    )
    commits = parse_log(out)
    assert [c.sha for c in commits] == ["aaa", "bbb"]
    assert commits[0].change_number == 65428
    assert commits[0].subject == "fix rsync"
    assert commits[1].change_number is None


def _runner(ok_refs=(), log_out="", show_out=""):
    ok = set(ok_refs)

    def run(args, cwd, timeout=60):
        if args[0] == "rev-parse":
            ref = args[-1]
            if ref in ok:
                return ref + "\n"
            raise GitError("unknown revision")
        if args[0] == "log":
            return log_out
        if args[0] == "show":
            return show_out
        raise AssertionError(f"unexpected git args: {args}")  # pragma: no cover

    return run


def test_pick_ref_prefers_origin():
    assert pick_ref(_runner(ok_refs=["origin/master", "master"]), "/c", "master") == "origin/master"


def test_pick_ref_falls_back_to_local():
    assert pick_ref(_runner(ok_refs=["b_es6_0"]), "/c", "b_es6_0") == "b_es6_0"


def test_pick_ref_not_found():
    with pytest.raises(GitError):
        pick_ref(_runner(ok_refs=[]), "/c", "ghost")


def test_commits_in_window_parses_runner_output():
    log_out = _rec("aaa", "2026-07-10T00:00:00+00:00", "Al", "s", "Reviewed-on: https://x/+/9\n")
    commits = commits_in_window(
        _runner(ok_refs=["origin/master"], log_out=log_out), "/c", "master", "2026-07-01", "2026-07-14"
    )
    assert commits[0].change_number == 9


def test_files_of():
    files = files_of(_runner(show_out="\nfs/foo.c\nfs/bar.c\n"), "/c", "abc")
    assert files == ["fs/foo.c", "fs/bar.c"]


# -- real subprocess (default_runner) ----------------------------------

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_default_runner_success_and_error(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "commit", "--allow-empty", "-qm", "first")
    out = default_runner(["rev-parse", "--verify", "HEAD"], str(tmp_path))
    assert len(out.strip()) == 40
    with pytest.raises(GitError):
        default_runner(["rev-parse", "--verify", "--quiet", "no-such-ref"], str(tmp_path))
