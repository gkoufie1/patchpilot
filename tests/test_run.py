"""The whole patch window against the in-memory lab, including both failure modes."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from patchpilot.checks import CheckResult, plan_remediation
from patchpilot.demo_data import generate
from patchpilot.models import find_customer, load_customers
from patchpilot.orchestrator import PatchRun, RunOptions
from patchpilot.planner import build_plan
from patchpilot.sim import SimLab

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def setup():
    customers = load_customers(ROOT / "config" / "customers.yaml")
    cust = find_customer(customers, "globex-industrial")
    certified = yaml.safe_load((ROOT / "pillar" / "certified.sls").read_text())["certified"]
    plan = build_plan(customers, generate(date(2026, 10, 17)), date(2026, 10, 19), 14)
    return cust, certified, plan


def _run(setup, tmp_path, lab=None, now=None, **opts):
    cust, certified, plan = setup
    window = plan.window_for(cust.name, "prod")
    clock_time = now or window.start + timedelta(minutes=5)
    run = PatchRun(lab or SimLab(), cust, cust.env("prod"), certified, plan, RunOptions(**opts),
                   tmp_path, clock=lambda: clock_time)
    return run, run.execute()


def test_clean_run_succeeds(setup, tmp_path):
    run, code = _run(setup, tmp_path, scan=True)
    assert code == 0 and run.rec.status == "success"
    assert (run.out_dir / "report.md").exists() and (run.out_dir / "run.json").exists()
    assert any("1 fixed" in s["summary"] for s in run.rec.scans)


def test_patch_order_is_db_then_app_then_windows(setup, tmp_path):
    lab = SimLab()
    _run(setup, tmp_path, lab=lab)
    patched = [t for t, fun, args, _ in lab.calls if fun == "state.apply" and args[0].startswith("patching")]
    assert patched == ["globex-db01", "globex-app01", "globex-win01"]
    stop = next(i for i, c in enumerate(lab.calls) if c[1] == "service.stop")
    first_patch = next(i for i, c in enumerate(lab.calls) if c[1] == "state.apply" and c[2][0] == "patching.linux")
    assert stop < first_patch   # application down before the database is touched


def test_selinux_reenabled_is_fixed_without_disabling_selinux(setup, tmp_path):
    lab = SimLab()
    run, code = _run(setup, tmp_path, lab=lab, inject="selinux")
    assert code == 0 and run.rec.status == "remediated"
    assert [r["action"] for r in run.rec.remediation] == ["selinux.app_policy"]
    app = lab.nodes["globex-app01"]
    assert app.selinux == "Enforcing" and app.httpd_can_network_connect


def test_unvetted_tomcat_is_repinned(setup, tmp_path):
    lab = SimLab()
    run, code = _run(setup, tmp_path, lab=lab, inject="tomcat")
    assert run.rec.status == "remediated"
    failed_first = {r["id"] for r in run.rec.validation if r["status"] == "fail"}
    assert failed_first == {"tomcat_version", "http_chart"}
    assert lab.nodes["globex-app01"].tomcat == "9.0.98"


def test_outside_window_aborts_before_touching_anything(setup, tmp_path):
    lab = SimLab()
    run, code = _run(setup, tmp_path, lab=lab, now=datetime(2026, 10, 20, 12, tzinfo=timezone.utc))
    assert code == 2 and run.rec.status == "aborted"
    assert not [c for c in lab.calls if c[1] == "state.apply"]


def test_broken_before_patching_aborts(setup, tmp_path):
    lab = SimLab()
    lab.nodes["globex-db01"].services["oracle-free"] = False
    run, code = _run(setup, tmp_path, lab=lab)
    assert run.rec.status == "aborted" and "pre-checks" in run.rec.steps[-1]["detail"]


def test_unfixable_failure_is_escalated_not_hidden(setup, tmp_path):
    lab = SimLab()
    # A chart that breaks while Tomcat is certified: that's an R&D code fix, not ours.
    patched = set()
    lab.after_patch.append(lambda n: patched.add(n.id))
    original = lab.http_get

    def http_get(url, timeout=15):
        if "chart" in url and "globex-app01" in patched:
            return 500, "Unable to compile class for JSP"
        return original(url)

    lab.http_get = http_get
    run, code = _run(setup, tmp_path, lab=lab)
    assert code == 1 and run.rec.status == "failed"
    assert run.rec.unresolved[0]["id"] == "http_chart"
    assert "R&D code fix" in (run.out_dir / "report.md").read_text()


def test_remediation_dedupes_and_maps_known_failures():
    results = [
        CheckResult("http_login", "app", "fail", "HTTP 502: Bad Gateway"),
        CheckResult("http_chart", "app", "fail", "HTTP 502: Bad Gateway"),
        CheckResult("selinux_denials", "app", "warn", "4 AVC denials"),
        CheckResult("service_nginx", "app", "fail", "not running"),
    ]
    fixes, unknown = plan_remediation(results)
    assert [(f.sls, f.command) for f in fixes] == [("selinux.app_policy", None), (None, ("service.restart", "nginx"))]
    assert not unknown


def test_host_silent_during_scan_is_reported_not_ok(setup, tmp_path):
    """Regression from the real lab: the DB froze mid-scan and the step still said 'ok'."""
    lab = SimLab()
    real_cmd = lab.salt.cmd

    def cmd(target, fun, *args, **kwargs):
        if fun == "cmd.run_all" and target == "globex-db01":
            return {}   # what Salt gives back when the minion never answers
        return real_cmd(target, fun, *args, **kwargs)

    lab.salt.cmd = cmd
    run, _ = _run(setup, tmp_path, lab=lab, scan=True)
    scan_step = next(s for s in run.rec.steps if s["step"] == "STIG scan (before)")
    assert scan_step["status"] == "warn"
    assert "globex-db01" in scan_step["detail"]
    assert [s["minion"] for s in run.rec.scans] == ["globex-app01"]


def test_low_memory_blocks_the_window(setup, tmp_path):
    lab = SimLab()
    lab.nodes["globex-db01"].mem_pct = 3
    run, code = _run(setup, tmp_path, lab=lab)
    assert code == 2
    assert "memory_available" in run.rec.steps[-1]["detail"]
