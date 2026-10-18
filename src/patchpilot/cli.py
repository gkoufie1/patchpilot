"""patchpilot: plan patch windows around batch workloads, patch with Salt, validate, report."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import history
from .models import Plan, find_customer, load_customers
from .planner import build_plan, plan_markdown

# Repo checkout holding config/ and pillar/ (on the lab master: /srv/patchpilot).
ROOT = Path(os.environ.get("PATCHPILOT_HOME", "."))


def _load_certified(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["certified"]


def _next_monday() -> date:
    today = date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


def cmd_demo_data(args) -> int:
    from .demo_data import generate

    runs = generate(date.fromisoformat(args.as_of) if args.as_of else date.today(), days=args.days)
    history.save_file(runs, args.out)
    print(f"wrote {len(runs)} job runs to {args.out}")
    return 0


def cmd_plan(args) -> int:
    customers = load_customers(args.config)
    if args.js7:
        client = history.JS7Client.from_env()
        if client is None:
            print("--js7 needs JS7_URL (and JS7_USER / JS7_PASSWORD)", file=sys.stderr)
            return 2
        runs = client.history(days=60)
    else:
        runs = history.load_file(args.history)
    start = date.fromisoformat(args.cycle_start) if args.cycle_start else _next_monday()
    plan = build_plan(customers, runs, start, args.days)
    if args.ai:
        from .ai import summarize
        plan.ai_summary = summarize(plan)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")
    md = plan_markdown(plan)
    (out / "plan.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"saved {out / 'plan.json'} and {out / 'plan.md'}")
    return 0


def _lab(args):
    if args.simulate:
        from .sim import SimLab
        return SimLab()
    from .lab import Lab
    return Lab()


def cmd_run(args) -> int:
    from .integrations import ServiceNow
    from .orchestrator import PatchRun, RunOptions

    cust = find_customer(load_customers(args.config), args.customer)
    env = cust.env(args.env)
    plan = Plan.from_dict(json.loads(Path(args.plan).read_text(encoding="utf-8"))) if Path(args.plan).exists() else None
    lab = _lab(args)
    opts = RunOptions(ignore_window=args.ignore_window, scan=args.scan,
                      rollback_on_fail=args.rollback_on_fail, inject=args.inject, patch_scope=args.scope)
    run = PatchRun(lab, cust, env, _load_certified(Path(args.certified)), plan, opts, Path(args.out),
                   snow=None if args.simulate else ServiceNow.from_env(),
                   js7=None if args.simulate else history.JS7Client.from_env())
    code = run.execute()
    print(f"\n{run.rec.status.upper()}: report at {run.out_dir / 'report.md'}")
    return code


def cmd_validate(args) -> int:
    from .checks import Validator, take_snapshot

    cust = find_customer(load_customers(args.config), args.customer)
    env = cust.env(args.env)
    certified = _load_certified(Path(args.certified))
    lab = _lab(args)
    baseline = {m: take_snapshot(lab, m, role, certified) for role, m in env.targets.items()}
    results = Validator(lab, certified, baseline).run(env.targets)
    for r in results:
        print(f"{r.status.upper():5} {r.target:14} {r.id:18} {r.detail}")
    return 1 if any(r.failed for r in results) else 0


def cmd_break(args) -> int:
    """Lab only: put the app node into one of the known-bad states, then try `validate`."""
    from .lab import Lab

    cust = find_customer(load_customers(args.config), args.customer)
    app = cust.env(args.env).targets["app"]
    lab = Lab()
    if args.scenario == "selinux":
        res = lab.salt.state_apply(app, "break.selinux_reenabled")
    else:
        ver = _load_certified(Path(args.certified))["lab_unvetted_tomcat"]
        res = lab.salt.state_apply(app, "tomcat", pillar={"tomcat": {"version": ver}})
    ok, errs = res.get(app, (False, ["no return"]))
    print("applied" if ok else f"failed: {errs}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="patchpilot", description=__doc__)
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--config", default=str(ROOT / "config" / "customers.yaml"))
        sp.add_argument("--certified", default=str(ROOT / "pillar" / "certified.sls"))

    sp = sub.add_parser("demo-data", help="write synthetic batch history for three customers")
    sp.add_argument("--out", default="data/history.json")
    sp.add_argument("--as-of", help="last day of history, YYYY-MM-DD (default today)")
    sp.add_argument("--days", type=int, default=60)
    sp.set_defaults(func=cmd_demo_data)

    sp = sub.add_parser("plan", help="find patch windows for the next cycle")
    common(sp)
    src = sp.add_mutually_exclusive_group()
    src.add_argument("--history", default="data/history.json", help="job history JSON file")
    src.add_argument("--js7", action="store_true", help="read history from JS7 (JS7_URL, JS7_USER, JS7_PASSWORD)")
    sp.add_argument("--cycle-start", help="YYYY-MM-DD (default next Monday)")
    sp.add_argument("--days", type=int, default=14, help="cycle length in days")
    sp.add_argument("--ai", action="store_true", help="add a Claude-written summary for the change review")
    sp.add_argument("--out", default="plans")
    sp.set_defaults(func=cmd_plan)

    for name, func, help_ in (("run", cmd_run, "patch one customer environment in its window"),
                              ("validate", cmd_validate, "run the post-patch checks now")):
        sp = sub.add_parser(name, help=help_)
        common(sp)
        sp.add_argument("--customer", required=True)
        sp.add_argument("--env", required=True)
        sp.add_argument("--simulate", action="store_true", help="use the in-memory lab instead of Salt")
        sp.set_defaults(func=func)
        if name == "run":
            sp.add_argument("--plan", default="plans/plan.json")
            sp.add_argument("--out", default="runs")
            sp.add_argument("--scope", choices=["security", "all"], default="security")
            sp.add_argument("--ignore-window", action="store_true", help="run now even if outside the window")
            sp.add_argument("--scan", action="store_true", help="OpenSCAP STIG scan before and after")
            sp.add_argument("--rollback-on-fail", action="store_true",
                            help="dnf history rollback if validation still fails after remediation")
            sp.add_argument("--inject", choices=["selinux", "tomcat"],
                            help="lab only: reproduce a known patching side effect after the app is patched")

    sp = sub.add_parser("break", help="lab only: put the app node into a known-bad state")
    common(sp)
    sp.add_argument("scenario", choices=["selinux", "tomcat"])
    sp.add_argument("--customer", default="globex-industrial")
    sp.add_argument("--env", default="prod")
    sp.set_defaults(func=cmd_break)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
