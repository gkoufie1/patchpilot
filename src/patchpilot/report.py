"""Markdown run report: what was done, what broke, what was fixed, what a human must look at."""
from __future__ import annotations

ICON = {"pass": "PASS", "warn": "WARN", "fail": "FAIL", "ok": "ok", "skip": "skip"}


def _checks_table(rows: list[dict]) -> list[str]:
    out = ["| Host | Check | Result | Detail |", "|---|---|---|---|"]
    out += [f"| {r['target']} | {r['id']} | {ICON.get(r['status'], r['status'])} | {r['detail']} |" for r in rows]
    return out


def _recommendations(rec) -> list[str]:
    recs = []
    actions = {r["action"] for r in rec.remediation}
    if "selinux.app_policy" in actions:
        recs.append("SELinux came back as Enforcing after patching. It was left Enforcing and the missing "
                    "policy was added (`httpd_can_network_connect`). Make Enforcing the baseline in the "
                    "`lab:selinux_mode` pillar: the RHEL 9 STIG requires it, and the next patch will not "
                    "surprise anyone.")
    if "tomcat" in actions:
        recs.append("Tomcat had moved off the R&D-certified version and was re-pinned. Find out what "
                    "upgraded it, and keep Tomcat outside OS patching.")
    for r in rec.unresolved:
        if r["id"] == "http_chart":
            recs.append(f"The chart screen on {r['target']} still fails ({r['detail']}) with a certified "
                        "Tomcat. That needs an R&D code fix; open a defect with the run report attached.")
        else:
            recs.append(f"{r['target']} {r['id']} still failing: {r['detail']}. Escalate before closing the window.")
    for s in rec.scans:
        if s["regressed"]:
            recs.append(f"New STIG failures on {s['minion']}: {', '.join(x.rsplit('_rule_', 1)[-1] for x in s['regressed'])}.")
    return recs


def render_run(rec) -> str:
    lines = [f"# Patch run {rec.run_id}", "",
             f"- **Customer / environment:** {rec.customer} / {rec.env}",
             f"- **Outcome:** {rec.status.upper()}",
             f"- **Started / finished (UTC):** {rec.started} / {rec.finished}"]
    if rec.window:
        lines.append(f"- **Planned window:** {rec.window['local']}")
    if rec.change:
        lines.append(f"- **Change request:** {rec.change['number']}")

    lines += ["", "## Timeline", "", "| Time (UTC) | Step | Status | Detail |", "|---|---|---|---|"]
    lines += [f"| {s['time'][11:19]} | {s['step']} | {s['status']} | {s['detail']} |" for s in rec.steps]

    before, after = rec.snapshots_before, rec.snapshots_after
    if before and after:
        lines += ["", "## What changed", "", "| Host | Item | Before | After |", "|---|---|---|---|"]
        for m, snap in before.items():
            for k, v in snap["data"].items():
                new = after.get(m, {}).get("data", {}).get(k)
                if new is not None and new != v:
                    lines.append(f"| {m} | {k} | {v} | {new} |")

    if rec.validation:
        lines += ["", "## Validation after patching", ""] + _checks_table(rec.validation)
    if rec.remediation:
        lines += ["", "## Automatic remediation", ""]
        lines += [f"- **{r['target']}**: `{r['action']}` ({'ok' if r['ok'] else 'FAILED'}). {r['why']}"
                  for r in rec.remediation]
    if rec.revalidation:
        lines += ["", "## Re-validation", ""] + _checks_table(rec.revalidation)
    if rec.scans:
        lines += ["", "## STIG scan (OpenSCAP)", ""] + [f"- {s['summary']}" for s in rec.scans]
    recs = _recommendations(rec)
    if recs:
        lines += ["", "## Follow-up", ""] + [f"- {r}" for r in recs]
    return "\n".join(lines) + "\n"
