"""Render pipeline results as human text or a JSON envelope.

Pure formatting only — no I/O, no gateway — so it is trivially testable.
"""

from __future__ import annotations

from typing import Any


def envelope(data: Any, command: str, ok: bool = True) -> dict:
    return {"ok": ok, "data": data, "meta": {"tool": "patch_finder", "command": command}}


def render_bisect(data: dict) -> str:
    t = data["target"]
    a = data["analysis"]
    w = data["window"]
    lines = [
        f"target: {t['suite']} {t['test']}  (job={t['job']}, base={t['base_branch']})"
    ]
    cfg = {k: v for k, v in (t.get("config") or {}).items() if v}
    if cfg:
        lines.append("  config: " + ", ".join(f"{k}={v}" for k, v in cfg.items()))
    if t.get("error_sample"):
        lines.append(f"  error : {t['error_sample'][:100]}")
    lines += [
        "",
        f"verdict: {a['classification'].upper()}   (window {w['from']}..{w['to']})",
        f"  samples={a['samples']} fails={a['fails']}   "
        f"rate {a['before_rate'] * 100:.1f}% -> {a['after_rate'] * 100:.1f}% "
        f"(delta {a['delta'] * 100:+.1f}pt, p={a['p_value']}, LR={a['lr']})",
    ]
    if a["last_good"]:
        lines.append(f"  transition: last-good {a['last_good']}  ->  first-bad {a['first_bad']}")

    if data["suspects"]:
        lines += ["", f"suspects (ranked, {len(data['suspects'])}):"]
        for i, s in enumerate(data["suspects"][:10], 1):
            ch = f"change {s['change_number']}" if s["change_number"] else "no-gerrit-link"
            lines.append(
                f"  {i}. [{s['score']:+.1f}] {s['sha'][:12]} {ch}  {s['subject'][:70]}"
            )
            for r in s["reasons"]:
                lines.append(f"        - {r}")

    if data.get("note"):
        lines += ["", "note: " + data["note"]]
    for warn in data.get("warnings", []):
        lines.append("warning: " + warn)
    if data.get("recommended_confirm"):
        lines += ["", "confirm the top suspect:", "  " + data["recommended_confirm"]]
    return "\n".join(lines)


def render_scan(data: dict) -> str:
    w = data["window"]
    lines = [
        f"job {data['job']}   window {w['from']}..{w['to']}   "
        f"sessions_examined={data['sessions_examined']}",
        "",
    ]
    if not data["candidates"]:
        lines.append("  no failures found in this window.")
    for i, c in enumerate(data["candidates"], 1):
        lines.append(
            f"  {i}. {c['suite']} {c['test']}   x{c['count']} in {c['session_count']} sessions"
        )
        if c.get("error_sample"):
            lines.append(f"        {c['error_sample'][:90]}")
        lines.append(f"        {c['bisect']}")
    for warn in data.get("warnings", []):
        lines.append("warning: " + warn)
    return "\n".join(lines)


def render_confirm(data: dict) -> str:
    lines = [f"confirm change {data['change']}: {data['target']}  mode={data['mode']}"]
    if data["mode"] == "dry-run":
        if not data["actions"]:
            lines.append("  no pre-landing sessions ran this test; nothing to retest.")
        else:
            lines.append(f"  would fire {len(data['actions'])} retest(s) (use --execute):")
            for a in data["actions"]:
                lines.append("    " + " ".join(a["command"]))
    elif data["mode"] == "execute":
        lines.append(f"  fired {len(data['results'])} retest(s):")
        for r in data["results"]:
            status = "ok" if r["returncode"] == 0 else f"FAILED rc={r['returncode']}"
            lines.append(f"    {r['session_id']}: {status}")
        lines.append("  re-run with --collect once the retests finish to read the new rate.")
    else:  # collect
        v = data["verdict"]
        lines.append(
            f"  current tally: {v['fail']}/{v['total']} failing "
            f"({(v['fail'] / v['total'] * 100) if v['total'] else 0:.1f}%)"
        )
    for warn in data.get("warnings", []):
        lines.append("warning: " + warn)
    return "\n".join(lines)
