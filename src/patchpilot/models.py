"""Data types shared by the planner, the orchestrator and the reports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class JobRun:
    """One finished batch workflow run, as reported by the job scheduler."""

    customer: str
    workflow: str
    start: datetime  # UTC
    end: datetime    # UTC

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def to_dict(self) -> dict:
        return {"customer": self.customer, "workflow": self.workflow,
                "start": iso(self.start), "end": iso(self.end)}

    @classmethod
    def from_dict(cls, d: dict) -> "JobRun":
        return cls(d["customer"], d["workflow"], parse_utc(d["start"]), parse_utc(d["end"]))


@dataclass
class EnvConfig:
    name: str                      # e.g. "nonprod", "prod"
    tier: str                      # "prod" or "nonprod"
    patch_minutes: int
    avoid_business_hours: bool
    month_end_blackout: bool
    preferred_days: list[int]      # 0 = Monday
    targets: dict[str, str]        # role -> Salt minion id / glob


@dataclass
class CustomerConfig:
    name: str
    segment: str                   # "federal" or "commercial"
    timezone: str
    buffer_minutes: int
    soak_days: int
    month_end_days: int
    business_hours: tuple[int, int]  # minutes after local midnight
    lookback_days: int
    environments: list[EnvConfig]

    def env(self, name: str) -> EnvConfig:
        for e in self.environments:
            if e.name == name:
                return e
        raise KeyError(f"{self.name} has no environment {name!r}")


def _hhmm(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


def load_customers(path: str | Path) -> list[CustomerConfig]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    defaults = raw.get("defaults", {})
    customers = []
    for c in raw["customers"]:
        merged = {**defaults, **c}
        bh_start, bh_end = merged.get("business_hours", "07:00-19:00").split("-")
        envs = []
        for e in c["environments"]:
            tier = e.get("tier", "prod" if e["name"].startswith("prod") else "nonprod")
            is_prod = tier == "prod"
            envs.append(EnvConfig(
                name=e["name"],
                tier=tier,
                patch_minutes=int(e.get("patch_minutes", 120)),
                avoid_business_hours=bool(e.get("avoid_business_hours", is_prod)),
                month_end_blackout=bool(e.get("month_end_blackout", is_prod)),
                preferred_days=[WEEKDAYS.index(d) for d in e.get("preferred_days", [])],
                targets=dict(e.get("targets", {})),
            ))
        customers.append(CustomerConfig(
            name=c["name"],
            segment=merged.get("segment", "commercial"),
            timezone=merged["timezone"],
            buffer_minutes=int(merged.get("buffer_minutes", 45)),
            soak_days=int(merged.get("soak_days", 3)),
            month_end_days=int(merged.get("month_end_days", 2)),
            business_hours=(_hhmm(bh_start), _hhmm(bh_end)),
            lookback_days=int(merged.get("lookback_days", 28)),
            environments=envs,
        ))
    return customers


def find_customer(customers: list[CustomerConfig], name: str) -> CustomerConfig:
    for c in customers:
        if c.name == name:
            return c
    raise KeyError(f"unknown customer {name!r}")


@dataclass
class Window:
    customer: str
    env: str
    start: datetime   # UTC
    end: datetime     # UTC
    timezone: str
    local: str        # human-readable local start/end
    day_type: str
    before: str       # nearest busy item before the window
    after: str        # nearest busy item after the window

    def to_dict(self) -> dict:
        d = asdict(self)
        d["start"], d["end"] = iso(self.start), iso(self.end)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Window":
        d = dict(d)
        d["start"], d["end"] = parse_utc(d["start"]), parse_utc(d["end"])
        return cls(**d)


@dataclass
class Drift:
    customer: str
    workflow: str
    baseline_minutes: float
    recent_minutes: float

    @property
    def change_pct(self) -> float:
        return (self.recent_minutes / self.baseline_minutes - 1) * 100


@dataclass
class Plan:
    generated_at: datetime
    cycle_start: str
    cycle_end: str
    windows: list[Window] = field(default_factory=list)
    unscheduled: list[dict] = field(default_factory=list)
    drift: list[Drift] = field(default_factory=list)
    ai_summary: str | None = None

    def window_for(self, customer: str, env: str) -> Window | None:
        return next((w for w in self.windows if w.customer == customer and w.env == env), None)

    def to_dict(self) -> dict:
        return {
            "generated_at": iso(self.generated_at),
            "cycle_start": self.cycle_start,
            "cycle_end": self.cycle_end,
            "windows": [w.to_dict() for w in self.windows],
            "unscheduled": self.unscheduled,
            "drift": [{**asdict(d), "change_pct": round(d.change_pct, 1)} for d in self.drift],
            "ai_summary": self.ai_summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Plan":
        return cls(
            generated_at=parse_utc(d["generated_at"]),
            cycle_start=d["cycle_start"],
            cycle_end=d["cycle_end"],
            windows=[Window.from_dict(w) for w in d["windows"]],
            unscheduled=d.get("unscheduled", []),
            drift=[Drift(x["customer"], x["workflow"], x["baseline_minutes"], x["recent_minutes"])
                   for x in d.get("drift", [])],
            ai_summary=d.get("ai_summary"),
        )
