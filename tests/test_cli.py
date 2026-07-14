import json
from datetime import date

import pytest
from click.testing import CliRunner

from patch_finder import cli, gitmap
from patch_finder.config import ConfigError, MalooCredentials
from patch_finder.gitmap import Commit, GitError
from patch_finder.maloo import MalooError

TSID = "16638759-936b-4590-add8-bdb5bd0eb287"
SID = "a22a650a-38a5-46bf-8de8-c055495d52c2"
URL = f"https://testing.whamcloud.com/test_sets/{TSID}"


@pytest.fixture
def runner():
    return CliRunner()


def _use(monkeypatch, gw):
    monkeypatch.setattr(cli, "_gateway", lambda: gw)


def _seed_regression(gw, job="lustre-reviews"):
    """A subtest that passes then starts failing, resolvable via its test_set URL."""
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    gw.add_session(SID, trigger_job=job, server_branch="master", server_file_system="ldiskfs")
    gw.add_test_set(TSID, SID, "SU", status="FAIL", submission="2026-07-20")
    gw.add_subtest(TSID, SID, "T2C", "FAIL", error="boom")
    for i in range(1, 11):
        s = f"p{i}"
        gw.add_session(s, trigger_job=job, server_file_system="ldiskfs")
        gw.add_test_set(f"pt{i}", s, "SU", status="PASS", submission=f"2026-07-{i:02d}")
    for i in range(11, 20):
        s = f"f{i}"
        gw.add_session(s, trigger_job=job, server_file_system="ldiskfs")
        gw.add_test_set(f"ft{i}", s, "SU", status="FAIL", submission=f"2026-07-{i:02d}")
        gw.add_subtest(f"ft{i}", s, "T2C", "FAIL")


# -- window_dates -------------------------------------------------------

def test_window_dates_fixed():
    assert cli.window_dates(7, date(2026, 7, 14)) == ("2026-07-07", "2026-07-14")


def test_window_dates_default_today():
    start, end = cli.window_dates(0)
    assert start == end


# -- top-level ----------------------------------------------------------

def test_help(runner):
    result = runner.invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    assert "Find the Lustre patch" in result.output


def test_gateway_config_error(runner, monkeypatch):
    monkeypatch.setattr(cli, "load_maloo_credentials",
                        lambda: (_ for _ in ()).throw(ConfigError("no creds")))
    result = runner.invoke(cli.main, ["scan", "--job", "b_es6_0"])
    assert result.exit_code != 0
    assert "no creds" in result.output


def test_gateway_success_then_config_parse_error(runner, monkeypatch):
    # _gateway builds a real (offline) gateway; --config parsing fails first.
    monkeypatch.setattr(cli, "load_maloo_credentials",
                        lambda: MalooCredentials("https://x", "u", "p"))
    result = runner.invoke(cli.main, ["bisect", "--url", URL, "--config", "bogus"])
    assert result.exit_code != 0
    assert "key=value" in result.output


# -- bisect -------------------------------------------------------------

def test_bisect_text(runner, gw, monkeypatch):
    _seed_regression(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: [])
    result = runner.invoke(cli.main, ["bisect", "--url", URL])
    assert result.exit_code == 0, result.output
    assert "target: lustre-rsync-test test_2c" in result.output
    assert "verdict:" in result.output


def test_bisect_json(runner, gw, monkeypatch):
    _seed_regression(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: [])
    result = runner.invoke(cli.main, ["bisect", "--url", URL, "--json"])
    assert result.exit_code == 0
    env = json.loads(result.output)
    assert env["ok"] and env["meta"]["tool"] == "patch_finder"


def test_bisect_by_name_with_suspect(runner, gw, monkeypatch):
    _seed_regression(gw, job="lustre-reviews")
    gw.add_session("spre", trigger_job="lustre-other")   # pre-landing lookup only, not a sample
    gw.add_test_set("spre-ts", "spre", "SU", status="FAIL", submission="2026-07-19")
    gw.add_subtest("spre-ts", "spre", "T2C", "FAIL")
    gw.link_review(100, "spre")
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window",
                        lambda *a, **k: [Commit("sha1", "2026-07-19", "A", "rsync fix", 100)])
    monkeypatch.setattr(gitmap, "files_of", lambda *a, **k: ["lustre/tests/lustre-rsync-test.sh"])
    result = runner.invoke(
        cli.main,
        ["bisect", "--suite", "lustre-rsync-test", "--test", "test_2c", "--job", "lustre-reviews"],
    )
    assert result.exit_code == 0, result.output
    assert "--change 100" in result.output


def test_bisect_resolve_error(runner, gw, monkeypatch):
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, ["bisect", "--suite", "ghost", "--test", "x", "--job", "lustre-master"])
    assert result.exit_code != 0
    assert "unknown suite" in result.output


def test_bisect_fetch_success(runner, gw, monkeypatch):
    _seed_regression(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: [])
    fetched = []
    monkeypatch.setattr(gitmap, "fetch", lambda run, clone, branch: fetched.append(branch))
    result = runner.invoke(cli.main, ["bisect", "--url", URL, "--fetch"])
    assert result.exit_code == 0
    assert fetched == ["master"]


def test_bisect_fetch_failure_is_swallowed(runner, gw, monkeypatch):
    _seed_regression(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: [])
    monkeypatch.setattr(gitmap, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(GitError("offline")))
    result = runner.invoke(cli.main, ["bisect", "--url", URL, "--fetch"])
    assert result.exit_code == 0
    assert "fetch failed" in result.output


# -- scan ---------------------------------------------------------------

def _seed_scan(gw):
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    gw.add_session("a", trigger_job="lustre-b_es6_0", test_sets_failed_count=1)
    gw.add_test_set("a-set", "a", "SU", status="FAIL")
    gw.add_subtest("a-set", "a", "T2C", "FAIL", error="boom")


def test_scan_text(runner, gw, monkeypatch):
    _seed_scan(gw)
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, ["scan", "--job", "b_es6_0"])
    assert result.exit_code == 0
    assert "lustre-rsync-test test_2c" in result.output


def test_scan_json(runner, gw, monkeypatch):
    _seed_scan(gw)
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, ["scan", "--job", "b_es6_0", "--json"])
    assert json.loads(result.output)["meta"]["command"] == "scan"


def test_scan_maloo_error(runner, gw, monkeypatch):
    _use(monkeypatch, gw)
    monkeypatch.setattr(cli, "run_scan",
                        lambda *a, **k: (_ for _ in ()).throw(MalooError("timeout")))
    result = runner.invoke(cli.main, ["scan", "--job", "b_es6_0"])
    assert result.exit_code != 0
    assert "timeout" in result.output


# -- confirm ------------------------------------------------------------

def _seed_confirm(gw):
    gw.name_set_script("SU", "lustre-rsync-test")
    gw.name_sub_script("T2C", "test_2c")
    gw.add_session("s100", trigger_job="lustre-reviews")
    gw.add_test_set("run", "s100", "SU", status="FAIL")
    gw.add_subtest("run", "s100", "T2C", "FAIL")
    gw.link_review(100, "s100")


def _confirm_args(*extra):
    return [
        "confirm", "--change", "100", "--suite", "lustre-rsync-test",
        "--test", "test_2c", "--job", "lustre-reviews", *extra,
    ]


def test_confirm_dry_run(runner, gw, monkeypatch):
    _seed_confirm(gw)
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, _confirm_args("--runs", "2"))
    assert result.exit_code == 0
    assert "would fire 2 retest" in result.output
    assert "placeholder" in result.output


def test_confirm_execute_requires_bug(runner, gw, monkeypatch):
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, _confirm_args("--execute"))
    assert result.exit_code != 0
    assert "requires --bug" in result.output


def test_confirm_execute(runner, gw, monkeypatch):
    _seed_confirm(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(cli.cf, "execute",
                        lambda actions: [{"session_id": "s100", "returncode": 0}])
    result = runner.invoke(cli.main, _confirm_args("--bug", "LU-1", "--runs", "1", "--execute"))
    assert result.exit_code == 0
    assert "fired 1 retest" in result.output


def test_confirm_max_sessions_warning(runner, gw, monkeypatch):
    _seed_confirm(gw)
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, _confirm_args("--runs", "1", "--max-sessions", "5"))
    assert result.exit_code == 0
    assert "considered at most 5" in result.output


def test_bisect_max_sessions_option(runner, gw, monkeypatch):
    _seed_regression(gw)
    _use(monkeypatch, gw)
    monkeypatch.setattr(gitmap, "commits_in_window", lambda *a, **k: [])
    result = runner.invoke(cli.main, ["bisect", "--url", URL, "--max-sessions", "3"])
    assert result.exit_code == 0
    assert "at most 3 pre-landing" in result.output


def test_confirm_collect(runner, gw, monkeypatch):
    _seed_confirm(gw)
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, _confirm_args("--collect"))
    assert result.exit_code == 0
    assert "current tally: 1/1" in result.output


def test_confirm_resolve_error(runner, gw, monkeypatch):
    _use(monkeypatch, gw)
    result = runner.invoke(cli.main, ["confirm", "--change", "5"])
    assert result.exit_code != 0
    assert "must pass --suite and --test" in result.output
