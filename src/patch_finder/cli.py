"""patch_finder command-line interface (thin: parse args, call pipeline, render)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

import click

from . import confirm as cf
from . import gitmap, report
from .config import DEFAULT_CLONE, ConfigError, branch_to_job, load_maloo_credentials
from .confirm import ConfirmError
from .gitmap import GitError, default_runner
from .maloo import MalooError, MalooGateway
from .pipeline import parse_config_filter, run_bisect, run_scan
from .resolve import ResolveError, resolve_target


def window_dates(days: int, today: date | None = None) -> tuple[str, str]:
    today = today or datetime.now(timezone.utc).date()
    start = (today - timedelta(days=days)).strftime("%Y-%m-%d")
    return start, today.strftime("%Y-%m-%d")


def _gateway() -> MalooGateway:
    try:
        creds = load_maloo_credentials()
    except ConfigError as exc:
        raise click.ClickException(str(exc))
    return MalooGateway(creds)


def _try_fetch(clone: str, branch: str) -> None:
    try:
        gitmap.fetch(default_runner, clone, branch)
    except GitError as exc:
        click.echo(f"warning: fetch failed: {exc}", err=True)


_RENDERERS = {
    "bisect": report.render_bisect,
    "scan": report.render_scan,
    "confirm": report.render_confirm,
}


def _emit(data: dict, command: str, as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(report.envelope(data, command), indent=2))
    else:
        click.echo(_RENDERERS[command](data))


@click.group()
@click.version_option(package_name="patch_finder")
def main() -> None:
    """Find the Lustre patch that introduced a Maloo test regression."""


@main.command()
@click.option("--url", default=None, help="Maloo test_set or test_session URL/UUID")
@click.option("--suite", default=None, help="Test suite, e.g. lustre-rsync-test")
@click.option("--test", default=None, help="Subtest, e.g. test_2c")
@click.option("--job", default=None, help="trigger_job or branch (b_es6_0, lustre-reviews, ...)")
@click.option("--branch", "base_branch", default=None, help="Override base git branch to bisect")
@click.option("--days", default=14, show_default=True, help="Look-back window in days")
@click.option("--config", "config_spec", default=None, help="Filter, e.g. 'fs=ldiskfs,distro=RHEL 8.10'")
@click.option("--clone", default=str(DEFAULT_CLONE), show_default=True, help="Local lustre-release clone")
@click.option("--fetch", is_flag=True, help="git fetch the branch before mapping")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON envelope")
def bisect(url, suite, test, job, base_branch, days, config_spec, clone, fetch, as_json):
    """Find the landed patch that introduced a test regression."""
    gw = _gateway()
    start, end = window_dates(days)
    resolved_job = branch_to_job(job) if job else None
    try:
        cfg = parse_config_filter(config_spec) if config_spec else None
        target = resolve_target(
            gw, url=url, suite=suite, test=test, job=resolved_job,
            base_branch=base_branch, from_date=start, to_date=end,
        )
        if fetch:
            _try_fetch(clone, target.base_branch)
        data = run_bisect(gw, target, start, end, clone=clone, config_filter=cfg)
    except (ResolveError, ValueError, GitError, MalooError) as exc:
        raise click.ClickException(str(exc))
    _emit(data, "bisect", as_json)


@main.command()
@click.option("--job", required=True, help="trigger_job or branch to scan")
@click.option("--days", default=10, show_default=True, help="Look-back window in days")
@click.option("--top", default=20, show_default=True, help="How many candidates to show")
@click.option("--max-sessions", "max_sessions", default=400, show_default=True, help="Session scan cap")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON envelope")
def scan(job, days, top, max_sessions, as_json):
    """Rank a branch's most frequent recent failures (candidates to bisect)."""
    gw = _gateway()
    start, end = window_dates(days)
    try:
        data = run_scan(gw, branch_to_job(job), start, end, top=top, max_sessions=max_sessions)
    except MalooError as exc:
        raise click.ClickException(str(exc))
    _emit(data, "scan", as_json)


@main.command()
@click.option("--change", type=int, required=True, help="Gerrit change number of the suspect")
@click.option("--url", default=None, help="Maloo URL to resolve the target from")
@click.option("--suite", default=None, help="Test suite")
@click.option("--test", default=None, help="Subtest")
@click.option("--job", default=None, help="trigger_job or branch")
@click.option("--branch", "base_branch", default=None, help="Override base git branch")
@click.option("--days", default=30, show_default=True, help="Look-back for target pinning")
@click.option("--bug", default=None, help="Justification JIRA ticket (required with --execute)")
@click.option("--runs", default=20, show_default=True, help="Number of retests to fire")
@click.option("--execute", is_flag=True, help="Actually fire the retests (default: dry-run)")
@click.option("--collect", is_flag=True, help="Read the current fail-rate instead of retesting")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON envelope")
def confirm(change, url, suite, test, job, base_branch, days, bug, runs, execute, collect, as_json):
    """Confirm a flaky suspect by re-running its pre-landing sessions."""
    gw = _gateway()
    start, end = window_dates(days)
    resolved_job = branch_to_job(job) if job else None
    if execute and not bug:
        raise click.ClickException("--execute requires --bug LU-xxxxx")
    try:
        target = resolve_target(
            gw, url=url, suite=suite, test=test, job=resolved_job,
            base_branch=base_branch, from_date=start, to_date=end,
        )
        label = f"{target.suite} {target.test}"
        if collect:
            data = {
                "change": change, "target": label, "mode": "collect",
                "verdict": cf.collect(gw, change, target), "warnings": [],
            }
        else:
            actions = cf.plan(gw, change, target, bug or "LU-XXXXX", runs)
            if execute:
                data = {
                    "change": change, "target": label, "mode": "execute",
                    "results": cf.execute(actions), "warnings": [],
                }
            else:
                data = {
                    "change": change, "target": label, "mode": "dry-run",
                    "actions": [{"command": a.command, "session_id": a.session_id} for a in actions],
                    "warnings": [] if bug else ["dry-run using placeholder ticket LU-XXXXX; pass --bug for real"],
                }
    except (ResolveError, ConfirmError, ValueError, MalooError) as exc:
        raise click.ClickException(str(exc))
    _emit(data, "confirm", as_json)


if __name__ == "__main__":  # pragma: no cover
    main()
