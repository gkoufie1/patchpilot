"""Synthetic batch history for three fictional customers.

Lets the planner run on a laptop with no scheduler. The shapes mirror what a
spare-parts planning customer runs: nightly ETL/forecast, weekly re-plans,
month-end close, and one customer whose nightly ETL grew after a new
warehouse came online (the runtime-drift case).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import WEEKDAYS, JobRun
from .planner import is_month_end


@dataclass(frozen=True)
class Schedule:
    workflow: str
    days: str | tuple[str, ...]   # "daily", "weekdays", "month_end", or weekday names
    at: str                        # local HH:MM
    minutes: int
    growth_days_ago: int | None = None   # runtime grows from this many days before as_of
    growth_factor: float = 1.0


CUSTOMERS: dict[str, tuple[str, list[Schedule]]] = {
    "acme-aero": ("America/New_York", [
        Schedule("nightly-forecast", "daily", "01:00", 150),
        Schedule("intraday-orders-sync", "weekdays", "12:30", 40),
        Schedule("weekend-replan", ("sat",), "22:00", 360),
        Schedule("month-end-close", "month_end", "18:00", 480),
    ]),
    "globex-industrial": ("Europe/Berlin", [
        Schedule("nightly-etl", "daily", "23:30", 200),
        Schedule("demand-sync", "weekdays", "12:00", 45),
        Schedule("sunday-optimization", ("sun",), "02:00", 300),
        Schedule("month-end-close", "month_end", "20:00", 420),
    ]),
    "initech-medical": ("America/Chicago", [
        Schedule("nightly-etl", "daily", "00:30", 120, growth_days_ago=14, growth_factor=1.45),
        Schedule("weekly-replan", ("wed",), "20:00", 180),
        Schedule("month-end-close", "month_end", "19:00", 360),
    ]),
}


def _runs_today(s: Schedule, d: date) -> bool:
    if s.days == "daily":
        return True
    if s.days == "weekdays":
        return d.weekday() < 5
    if s.days == "month_end":
        return is_month_end(d, 2)
    return WEEKDAYS[d.weekday()] in s.days


def generate(as_of: date, days: int = 60, seed: int = 42) -> list[JobRun]:
    rng = random.Random(seed)
    cutoff = datetime.combine(as_of, time(0), tzinfo=timezone.utc)
    runs: list[JobRun] = []
    for customer, (tzname, schedules) in CUSTOMERS.items():
        tz = ZoneInfo(tzname)
        for offset in range(days, 0, -1):
            d = as_of - timedelta(days=offset)
            for s in schedules:
                if not _runs_today(s, d):
                    continue
                h, m = map(int, s.at.split(":"))
                start = datetime.combine(d, time(h, m), tzinfo=tz) + timedelta(minutes=rng.uniform(-10, 10))
                minutes = s.minutes * rng.uniform(0.88, 1.12)
                if s.growth_days_ago is not None and offset <= s.growth_days_ago:
                    minutes *= s.growth_factor
                end = start + timedelta(minutes=minutes)
                start_utc, end_utc = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
                if end_utc <= cutoff:   # only finished runs
                    runs.append(JobRun(customer, s.workflow, start_utc.replace(microsecond=0),
                                       end_utc.replace(microsecond=0)))
    return sorted(runs, key=lambda r: r.start)
