"""One patch window, end to end.

  gate      inside the planned window? no batch workflow running? hosts reachable?
  record    change request, snapshot of what we pin, optional STIG scan, pre-checks
  patch     app down -> DB patch/reboot -> app patch/reboot -> Windows patch/reboot
  validate  the runbook checks
  fix       known failures get known fixes, then validate again
  rollback  optional, if failures remain
  report    Markdown + JSON, S3, chat, change request closed with the outcome
"""
from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import scap
from .checks import CheckResult, Snapshot, Validator, plan_remediation, take_snapshot
from .integrations import ServiceNow, notify, upload_dir
from .lab import Lab
from .models import CustomerConfig, EnvConfig, Plan, iso

log = logging.getLogger("patchpilot")

ORDER = ("db", "app", "win")   # database first while the app is down; Windows app tier last


class Abort(Exception):
    pass


@dataclass
class RunOptions:
    ignore_window: bool = False
    scan: bool = False
    rollback_on_fail: bool = False
    inject: str | None = None      # lab only: "selinux" or "tomcat"
    patch_scope: str = "security"  # "security" or "all"


@dataclass
class RunRecord:
    run_id: str
    customer: str
    env: str
    started: str
    status: str = "running"         # success | remediated | failed | aborted
    window: dict | None = None
    change: dict | None = None
    steps: list[dict] = field(default_factory=list)
    snapshots_before: dict = field(default_factory=dict)
    snapshots_after: dict = field(default_factory=dict)
    prechecks: list[dict] = field(default_factory=list)
    validation: list[dict] = field(default_factory=list)
    remediation: list[dict] = field(default_factory=list)
    revalidation: list[dict] = field(default_factory=list)
    unresolved: list[dict] = field(default_factory=list)
    scans: list[dict] = field(default_factory=list)
    finished: str | None = None


def _dump(results: list[CheckResult]) -> list[dict]:
    return [r.__dict__.copy() for r in results]


class PatchRun:
    def __init__(self, lab: Lab, cust: CustomerConfig, env: EnvConfig, certified: dict,
                 plan: Plan | None, opts: RunOptions, out_dir: Path,
                 snow: ServiceNow | None = None, js7=None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.lab, self.cust, self.env, self.certified = lab, cust, env, certified
        self.plan, self.opts, self.snow, self.js7, self.clock = plan, opts, snow, js7, clock
        now = clock()
        self.rec = RunRecord(run_id=f"{now:%Y%m%dT%H%M%SZ}-{cust.name}-{env.name}-{secrets.token_hex(2)}",
                             customer=cust.name, env=env.name, started=iso(now))
        self.out_dir = out_dir / self.rec.run_id
        self.targets = {r: env.targets[r] for r in ORDER if r in env.targets}
        self.baseline: dict[str, Snapshot] = {}

    # -- bookkeeping -------------------------------------------------------------
    def step(self, name: str, status: str = "ok", detail: str = "") -> None:
        self.rec.steps.append({"time": iso(self.clock()), "step": name, "status": status, "detail": detail})
        log.info("%-28s %-6s %s", name, status, detail)

    def _snow_note(self, note: str) -> None:
        if self.snow and self.rec.change:
            try:
                self.snow.work_note(self.rec.change["sys_id"], note)
            except Exception as exc:  # a ServiceNow outage must not break the window
                log.warning("ServiceNow work note failed: %s", exc)

    # -- entry point -------------------------------------------------------------
    def execute(self) -> int:
        try:
            self._gate()
            self._record()
            self._patch()
            self._validate_and_fix()
            self._post_scan()
        except Abort as exc:
            self.rec.status = "aborted"
            self.step("aborted", "fail", str(exc))
            self._restore_app()
        finally:
            self.rec.finished = iso(self.clock())
            self._report()
        return {"success": 0, "remediated": 0, "failed": 1}.get(self.rec.status, 2)

    # -- phases --------------------------------------------------------------------
    def _gate(self) -> None:
        if not self.targets:
            raise Abort(f"{self.cust.name}/{self.env.name} has no Salt targets in customers.yaml")
        window = self.plan.window_for(self.cust.name, self.env.name) if self.plan else None
        if window:
            self.rec.window = window.to_dict()
        now = self.clock()
        if self.opts.ignore_window:
            self.step("window check", "warn", "--ignore-window: running outside the planned window")
        elif window is None:
            raise Abort("no planned window for this environment; run `patchpilot plan` first")
        elif not (window.start <= now <= window.end):
            raise Abort(f"now ({iso(now)}) is outside the planned window {window.local}")
        else:
            self.step("window check", "ok", window.local)

        if self.js7:
            running = self.js7.running(self.cust.name)
            if running:
                raise Abort(f"batch workflows still running: {', '.join(running)}")
            self.step("batch check", "ok", "no customer workflows running in JS7")

        missing = self.lab.wait_up(list(self.targets.values()), timeout=60)
        if missing:
            raise Abort(f"minions not responding: {', '.join(missing)}")
        self.step("reachability", "ok", ", ".join(self.targets.values()))

    def _record(self) -> None:
        if self.snow:
            w = self.rec.window
            start = datetime.fromisoformat(w["start"].replace("Z", "+00:00")) if w else self.clock()
            end = datetime.fromisoformat(w["end"].replace("Z", "+00:00")) if w else self.clock()
            try:
                self.rec.change = self.snow.create_change(
                    f"Patching {self.cust.name} {self.env.name}",
                    f"Automated {self.opts.patch_scope} patching by PatchPilot run {self.rec.run_id}. "
                    f"Targets: {', '.join(self.targets.values())}.", start, end)
                self.step("change request", "ok", self.rec.change["number"])
            except Exception as exc:
                self.step("change request", "warn", f"could not create: {exc}")
        notify(f"PatchPilot: starting {self.cust.name}/{self.env.name} ({self.rec.run_id})")

        for role, m in self.targets.items():
            self.baseline[m] = take_snapshot(self.lab, m, role, self.certified)
            self.rec.snapshots_before[m] = self.baseline[m].to_dict()
        self.step("snapshot", "ok", "pinned versions, SELinux state, last dnf transaction")

        if self.opts.scan:
            results = {m: scap.scan(self.lab, m, "before") for r, m in self.targets.items() if r != "win"}
            self._scan_before = {m: r for m, r in results.items() if r}
            silent = sorted(m for m, r in results.items() if not r)
            if silent:
                self.step("STIG scan (before)", "warn",
                          f"no results from {', '.join(silent)}: host did not answer during the scan")
            else:
                self.step("STIG scan (before)", "ok", f"{len(results)} hosts")

        pre = Validator(self.lab, self.certified, self.baseline).run(self.targets)
        self.rec.prechecks = _dump(pre)
        failing = [r for r in pre if r.failed]
        if failing:
            raise Abort("pre-checks failing before any change was made: "
                        + "; ".join(f"{r.target} {r.id}: {r.detail}" for r in failing))
        self.step("pre-checks", "ok", f"{len(pre)} checks passed or warned")

    def _reboot_if_needed(self, role: str, m: str) -> None:
        windows = role == "win"
        if windows:
            needed = bool(self.lab.salt.cmd(m, "system.get_pending_reboot").get(m))
        else:
            needed = self.lab.salt.cmd(m, "cmd.retcode", "needs-restarting -r").get(m) == 1
        if not needed:
            self.step(f"reboot {m}", "skip", "not required")
            return
        self.lab.salt.reboot(m, windows=windows)
        self.lab.sleep(90)
        if self.lab.wait_up([m], timeout=1200):
            raise Abort(f"{m} did not come back after reboot")
        self.step(f"reboot {m}", "ok", "back online")

    def _patch(self) -> None:
        pillar = {"patch_scope": self.opts.patch_scope}
        if "app" in self.targets:
            self.lab.salt.cmd(self.targets["app"], "service.stop", "tomcat")
            self._app_stopped = True
            self.step("stop application", "ok", f"tomcat on {self.targets['app']}")
        for role in ORDER:
            m = self.targets.get(role)
            if not m:
                continue
            sls = "patching.windows" if role == "win" else "patching.linux"
            result = self.lab.salt.state_apply(m, sls, pillar=pillar).get(m)
            if result is None:
                raise Abort(f"{m} returned nothing for {sls}")
            ok, fails = result
            if not ok:
                self._snow_note(f"{sls} failed on {m}: {fails}")
                raise Abort(f"{sls} failed on {m}: {'; '.join(fails)}")
            self.step(f"patch {m}", "ok", sls)
            self._reboot_if_needed(role, m)
            if role == "app":
                self._inject(m)
                self.lab.salt.cmd(m, "service.start", "tomcat")
                self._app_stopped = False
        self._snow_note("Patching complete; running validation.")

    def _restore_app(self) -> None:
        """An abort after the app was stopped must not leave customers down."""
        if not getattr(self, "_app_stopped", False):
            return
        app = self.targets["app"]
        try:
            ok = bool(self.lab.salt.cmd(app, "service.start", "tomcat").get(app))
        except Exception as exc:
            ok = False
            log.warning("could not restart tomcat: %s", exc)
        self.step("restore application", "ok" if ok else "fail",
                  f"tomcat on {app} {'started again' if ok else 'STILL DOWN: start it by hand'}")
        self._snow_note(f"Window aborted; application on {app} {'restored' if ok else 'still down'}.")

    def _inject(self, m: str) -> None:
        """Lab only: reproduce a real side effect of patching after the patch lands."""
        if self.opts.inject == "selinux":
            self.lab.salt.state_apply(m, "break.selinux_reenabled")
            self.step("inject (lab)", "warn", "SELinux switched to Enforcing, as a patch once did")
        elif self.opts.inject == "tomcat":
            ver = self.certified["lab_unvetted_tomcat"]
            self.lab.salt.state_apply(m, "tomcat", pillar={"tomcat": {"version": ver}})
            self.step("inject (lab)", "warn", f"Tomcat moved to unvetted {ver}")

    def _validate_and_fix(self) -> None:
        validator = Validator(self.lab, self.certified, self.baseline)
        results = validator.run(self.targets)
        self.rec.validation = _dump(results)
        failing = [r for r in results if r.failed]
        if not failing:
            self.rec.status = "success"
            self.step("validation", "ok", f"{len(results)} checks, no failures")
            return
        self.step("validation", "fail", "; ".join(f"{r.target} {r.id}" for r in failing))

        fixes, unknown = plan_remediation(results)
        for f in fixes:
            if f.sls:
                ok, errs = self.lab.salt.state_apply(f.target, f.sls).get(f.target, (False, ["no return"]))
            else:
                ok, errs = bool(self.lab.salt.cmd(f.target, *f.command).get(f.target)), []
            self.rec.remediation.append({"target": f.target, "action": f.sls or " ".join(f.command),
                                         "why": f.why, "ok": ok, "errors": errs})
            self.step(f"remediate {f.target}", "ok" if ok else "fail", f.why)
        if fixes:
            results = validator.run(self.targets)
            self.rec.revalidation = _dump(results)

        failing = [r for r in results if r.failed]
        if not failing:
            self.rec.status = "remediated"
            self.step("re-validation", "ok", "all failures fixed automatically")
            return
        self.rec.unresolved = _dump(failing)
        if not fixes:
            self.step("remediation", "fail", "no automated fix known: "
                      + "; ".join(f"{r.target} {r.id}" for r in unknown))
        if self.opts.rollback_on_fail:
            self._rollback()
        self.rec.status = "failed"

    def _rollback(self) -> None:
        for role, m in self.targets.items():
            if role == "win":
                continue   # Windows updates roll back from the VM snapshot, not from here
            txn = self.baseline[m].data.get("dnf_txn")
            if txn and txn.isdigit():
                self.lab.salt.cmd(m, "cmd.run", f"dnf -y history rollback {txn}", timeout=1800)
                self.step(f"rollback {m}", "warn", f"dnf history rollback {txn}")
                self._reboot_if_needed(role, m)
        results = Validator(self.lab, self.certified, self.baseline).run(self.targets)
        self.rec.revalidation = _dump(results)

    def _post_scan(self) -> None:
        for role, m in self.targets.items():
            self.rec.snapshots_after[m] = take_snapshot(self.lab, m, role, self.certified).to_dict()
        if not self.opts.scan:
            return
        for m, before in getattr(self, "_scan_before", {}).items():
            after = scap.scan(self.lab, m, "after")
            if not after:
                self.step("STIG scan (after)", "warn", f"no results from {m}")
                continue
            d = scap.diff(m, before, after)
            self.rec.scans.append({"minion": m, "summary": d.summary, "fixed": d.fixed, "regressed": d.regressed})
            self.step("STIG scan (after)", "ok", d.summary)

    # -- output ----------------------------------------------------------------------
    def _report(self) -> None:
        from .report import render_run

        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "run.json").write_text(json.dumps(self.rec.__dict__, indent=2), encoding="utf-8")
        md = render_run(self.rec)
        (self.out_dir / "report.md").write_text(md, encoding="utf-8")
        try:
            for key in upload_dir(self.out_dir, f"runs/{self.rec.run_id}"):
                log.info("uploaded %s", key)
        except Exception as exc:
            log.warning("S3 upload failed: %s", exc)
        notify(f"PatchPilot: {self.cust.name}/{self.env.name} finished: {self.rec.status.upper()} "
               f"({self.rec.run_id})")
        if self.snow and self.rec.change:
            try:
                self.snow.close(self.rec.change["sys_id"], self.rec.status in ("success", "remediated"),
                                md[:3900])
            except Exception as exc:
                log.warning("ServiceNow close failed: %s", exc)
