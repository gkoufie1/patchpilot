"""Find safe patch windows from batch-job history.

The idea: a customer's batch workflows follow a rhythm that depends on the
kind of day (a specific weekday, or month-end). From recent history we build a
profile per (workflow, day key) -- earliest typical start and p95 finish -- and
project those profiles onto every day of the coming cycle. Business hours and
month-end blackouts are added as busy time, then we look for a gap long enough
for the patch, preferring the days each environment prefers. Non-prod goes
first; prod waits for a soak period after non-prod.
"""
from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import WEEKDAYS, CustomerConfig, Drift, EnvConfig, JobRun, Plan, Window

DRIFT_THRESHOLD = 1.20   # flag a workflow whose recent median runtime grew 20%+
DRIFT_RECENT_DAYS = 14


def is_month_end(d: date, month_end_days: int) -> bool:
    last = calendar.monthrange(d.year, d.month)[1]
    return d.day > last - month_end_days


def day_key(d: date, month_end_days: int) -> str:
    """'month_end' overrides the weekday, because month-end runs are different."""
    return "month_end" if is_month_end(d, month_end_days) else WEEKDAYS[d.weekday()]


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; small samples stay conservative."""
    ordered = sorted(values)
    rank = max(1, min(len(ordered), round(pct / 100 * len(ordered) + 0.5)))
    return ordered[rank - 1]


@dataclass(frozen=True)
class Profile:
    workflow: str
    day_key: str
    start_min: float    # earliest typical start, minutes after local midnight
    finish_min: float   # p95 finish; may exceed 1440 when a run spills past midnight
    samples: int


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime
    label: str


def build_profiles(runs: list[JobRun], cust: CustomerConfig, as_of: datetime) -> list[Profile]:
    tz = ZoneInfo(cust.timezone)
    since = as_of - timedelta(days=cust.lookback_days)
    groups: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for r in runs:
        if r.customer != cust.name or r.start < since:
            continue
        local = r.start.astimezone(tz)
        start_min = local.hour * 60 + local.minute
        groups[(r.workflow, day_key(local.date(), cust.month_end_days))].append(
            (start_min, start_min + r.minutes))
    return [
        Profile(wf, key,
                start_min=percentile([s for s, _ in vals], 5),
                finish_min=percentile([f for _, f in vals], 95),
                samples=len(vals))
        for (wf, key), vals in sorted(groups.items())
    ]


def detect_drift(runs: list[JobRun], cust: CustomerConfig, as_of: datetime) -> list[Drift]:
    """Customers change things (new warehouses, more parts); runtimes creep up.
    Flag workflows whose recent median runtime is well above their baseline."""
    recent_from = as_of - timedelta(days=DRIFT_RECENT_DAYS)
    baseline_from = as_of - timedelta(days=cust.lookback_days * 2)
    recent: dict[str, list[float]] = defaultdict(list)
    baseline: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        if r.customer != cust.name or r.start < baseline_from:
            continue
        (recent if r.start >= recent_from else baseline)[r.workflow].append(r.minutes)
    drift = []
    for wf in sorted(recent):
        if len(recent[wf]) < 3 or len(baseline[wf]) < 3:
            continue
        rec, base = statistics.median(recent[wf]), statistics.median(baseline[wf])
        if base > 0 and rec / base >= DRIFT_THRESHOLD:
            drift.append(Drift(cust.name, wf, round(base, 1), round(rec, 1)))
    return drift


def _at(d: date, minutes: float, tz: ZoneInfo) -> datetime:
    return (datetime.combine(d, time(0), tzinfo=tz) + timedelta(minutes=minutes)).astimezone(timezone.utc)


def busy_timeline(cust: CustomerConfig, env: EnvConfig, profiles: list[Profile],
                  first: date, last: date) -> list[Busy]:
    """Every predicted busy interval from the day before `first` through the day after `last`."""
    tz = ZoneInfo(cust.timezone)
    buf = cust.buffer_minutes
    by_key: dict[str, list[Profile]] = defaultdict(list)
    for p in profiles:
        by_key[p.day_key].append(p)

    busy: list[Busy] = []
    d = first - timedelta(days=1)
    while d <= last + timedelta(days=1):
        key = day_key(d, cust.month_end_days)
        for p in by_key.get(key, []):
            busy.append(Busy(_at(d, p.start_min - buf, tz), _at(d, p.finish_min + buf, tz),
                             f"{p.workflow} ({key})"))
        if env.avoid_business_hours and d.weekday() < 5:
            busy.append(Busy(_at(d, cust.business_hours[0], tz), _at(d, cust.business_hours[1], tz),
                             "business hours"))
        if env.month_end_blackout and key == "month_end":
            busy.append(Busy(_at(d, 0, tz), _at(d, 1440, tz), "month-end blackout"))
        d += timedelta(days=1)
    return sorted(busy, key=lambda b: b.start)


def free_gaps(busy: list[Busy], start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    gaps, cursor = [], start
    for b in busy:
        if b.end <= cursor:
            continue
        if b.start >= end:
            break
        if b.start > cursor:
            gaps.append((cursor, b.start))
        cursor = max(cursor, b.end)
    if cursor < end:
        gaps.append((cursor, end))
    return gaps


def _candidates(gaps, minutes: int, tz: ZoneInfo):
    """Gap starts, plus each local midnight inside a long gap, so a gap that opens
    Friday evening can still yield a window that starts on Saturday."""
    need = timedelta(minutes=minutes)
    for g_start, g_end in gaps:
        if g_end - g_start < need:
            continue
        yield g_start
        local = g_start.astimezone(tz)
        nxt = datetime.combine(local.date() + timedelta(days=1), time(0), tzinfo=tz).astimezone(timezone.utc)
        while nxt + need <= g_end:
            yield nxt
            nxt = (nxt.astimezone(tz) + timedelta(days=1)).astimezone(timezone.utc)


def _neighbours(busy: list[Busy], start: datetime, end: datetime) -> tuple[str, str]:
    before = max((b for b in busy if b.end <= start), key=lambda b: b.end, default=None)
    after = min((b for b in busy if b.start >= end), key=lambda b: b.start, default=None)
    return (before.label if before else "-", after.label if after else "-")


def schedule_env(cust: CustomerConfig, env: EnvConfig, profiles: list[Profile],
                 earliest: datetime, cycle_end: date) -> Window | None:
    tz = ZoneInfo(cust.timezone)
    horizon_end = _at(cycle_end + timedelta(days=1), 0, tz)
    busy = busy_timeline(cust, env, profiles, earliest.astimezone(tz).date(), cycle_end)
    gaps = free_gaps(busy, earliest, horizon_end)
    cands = list(_candidates(gaps, env.patch_minutes, tz))
    if not cands:
        return None
    preferred = [c for c in cands if c.astimezone(tz).weekday() in env.preferred_days]
    start = (preferred or cands)[0]
    end = start + timedelta(minutes=env.patch_minutes)
    ls, le = start.astimezone(tz), end.astimezone(tz)
    before, after = _neighbours(busy, start, end)
    return Window(
        customer=cust.name, env=env.name, start=start, end=end, timezone=cust.timezone,
        local=f"{ls:%a %Y-%m-%d %H:%M} -> {le:%a %H:%M} {ls.tzname()}",
        day_type=day_key(ls.date(), cust.month_end_days),
        before=before, after=after,
    )


def build_plan(customers: list[CustomerConfig], runs: list[JobRun], cycle_start: date,
               cycle_days: int, as_of: datetime | None = None) -> Plan:
    as_of = as_of or max((r.end for r in runs), default=datetime.now(timezone.utc))
    cycle_end = cycle_start + timedelta(days=cycle_days - 1)
    plan = Plan(generated_at=datetime.now(timezone.utc),
                cycle_start=cycle_start.isoformat(), cycle_end=cycle_end.isoformat())
    for cust in customers:
        tz = ZoneInfo(cust.timezone)
        profiles = build_profiles(runs, cust, as_of)
        plan.drift.extend(detect_drift(runs, cust, as_of))
        earliest = _at(cycle_start, 0, tz)
        # Non-prod first; prod only after non-prod has soaked.
        for env in sorted(cust.environments, key=lambda e: e.tier == "prod"):
            win = schedule_env(cust, env, profiles, earliest, cycle_end)
            if win is None:
                plan.unscheduled.append({"customer": cust.name, "env": env.name,
                                         "reason": f"no {env.patch_minutes}-minute gap in the cycle"})
                continue
            plan.windows.append(win)
            if env.tier != "prod":
                earliest = max(earliest, win.end + timedelta(days=cust.soak_days))
    plan.windows.sort(key=lambda w: w.start)
    return plan


def plan_markdown(plan: Plan) -> str:
    lines = [f"# Patch plan: {plan.cycle_start} to {plan.cycle_end}", ""]
    if plan.ai_summary:
        lines += ["## Summary", "", plan.ai_summary.strip(), ""]
    lines += ["## Windows", "",
              "| Customer | Env | Local window | UTC start | Day | Starts after | Must end before |",
              "|---|---|---|---|---|---|---|"]
    for w in plan.windows:
        lines.append(f"| {w.customer} | {w.env} | {w.local} | {w.start:%Y-%m-%d %H:%M} | "
                     f"{w.day_type} | {w.before} | {w.after} |")
    if plan.unscheduled:
        lines += ["", "## Could not schedule", ""]
        lines += [f"- **{u['customer']} / {u['env']}**: {u['reason']}" for u in plan.unscheduled]
    if plan.drift:
        lines += ["", "## Runtime drift (check with the customer before the window)", ""]
        lines += [f"- **{d.customer} / {d.workflow}**: median {d.baseline_minutes:.0f} min to "
                  f"{d.recent_minutes:.0f} min (+{d.change_pct:.0f}%) in the last {DRIFT_RECENT_DAYS} days"
                  for d in plan.drift]
    return "\n".join(lines) + "\n"
