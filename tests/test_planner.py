from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from patchpilot.demo_data import generate
from patchpilot.models import JobRun, find_customer, load_customers
from patchpilot.planner import build_plan, build_profiles, busy_timeline, day_key, is_month_end

CONFIG = Path(__file__).resolve().parents[1] / "config" / "customers.yaml"
AS_OF = date(2026, 10, 17)
CYCLE = date(2026, 10, 19)


@pytest.fixture(scope="module")
def customers():
    return load_customers(CONFIG)


@pytest.fixture(scope="module")
def runs():
    return generate(AS_OF)


@pytest.fixture(scope="module")
def plan(customers, runs):
    return build_plan(customers, runs, CYCLE, 14)


def test_month_end_overrides_weekday():
    assert is_month_end(date(2026, 10, 30), 2) and is_month_end(date(2026, 10, 31), 2)
    assert not is_month_end(date(2026, 10, 29), 2)
    assert day_key(date(2026, 10, 31), 2) == "month_end"   # a Saturday, but month-end wins
    assert day_key(date(2026, 10, 24), 2) == "sat"


def test_every_environment_gets_a_window(plan, customers):
    assert not plan.unscheduled
    assert len(plan.windows) == sum(len(c.environments) for c in customers)


def test_windows_never_overlap_predicted_busy_time(plan, customers, runs):
    as_of = max(r.end for r in runs)
    for w in plan.windows:
        cust = find_customer(customers, w.customer)
        env = cust.env(w.env)
        busy = busy_timeline(cust, env, build_profiles(runs, cust, as_of), CYCLE, CYCLE + timedelta(days=13))
        for b in busy:
            assert w.end <= b.start or w.start >= b.end, f"{w.customer}/{w.env} overlaps {b.label}"


def test_prod_waits_for_nonprod_soak_and_lands_on_weekend(plan, customers):
    for cust in customers:
        nonprod, prod = plan.window_for(cust.name, "nonprod"), plan.window_for(cust.name, "prod")
        assert prod.start >= nonprod.end + timedelta(days=cust.soak_days)
        assert prod.start.astimezone(ZoneInfo(cust.timezone)).weekday() in (5, 6)


def test_prod_avoids_month_end(plan, customers):
    for w in plan.windows:
        if w.env == "prod":
            assert w.day_type != "month_end"


def test_window_clears_a_run_that_spills_past_midnight(plan):
    # globex nightly-etl starts ~23:30 and runs ~200 min, so Saturday is busy until ~03:00 + buffer.
    w = plan.window_for("globex-industrial", "prod")
    local = w.start.astimezone(ZoneInfo("Europe/Berlin"))
    assert local.hour * 60 + local.minute >= 3 * 60 + 15
    assert "nightly-etl" in w.before


def test_runtime_drift_is_flagged(plan):
    drift = {(d.customer, d.workflow): d for d in plan.drift}
    assert ("initech-medical", "nightly-etl") in drift
    assert drift[("initech-medical", "nightly-etl")].change_pct > 30
    assert len(drift) == 1   # stable workflows are not flagged


def test_growth_pushes_the_window_later(customers, runs):
    """If the nightly ETL keeps growing, the planner must move the window, not overlap it."""
    grown = [JobRun(r.customer, r.workflow, r.start, r.start + (r.end - r.start) * 1.6)
             if (r.customer, r.workflow) == ("initech-medical", "nightly-etl") else r for r in runs]
    before = build_plan(customers, runs, CYCLE, 14).window_for("initech-medical", "nonprod")
    after = build_plan(customers, grown, CYCLE, 14).window_for("initech-medical", "nonprod")
    assert after.start > before.start


def test_plan_round_trips_through_json(plan):
    from patchpilot.models import Plan
    again = Plan.from_dict(plan.to_dict())
    assert [w.start for w in again.windows] == [w.start for w in plan.windows]
    assert isinstance(again.generated_at, datetime) and again.generated_at.tzinfo == timezone.utc
