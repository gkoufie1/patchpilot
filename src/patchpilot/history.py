"""Where batch history comes from: a JSON file, or a live JS7 JobScheduler.

JS7 (the current SOS JobScheduler) exposes a REST API on JOC Cockpit. Workflows
are expected under /customers/<customer>/<workflow>, so the folder tells us
which customer a run belongs to. Endpoint and field names follow the JS7 REST
API documentation; check them against your JOC version (JOC Cockpit ships
its API docs at /joc/api-docs).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from .models import JobRun, parse_utc


def load_file(path: str | Path) -> list[JobRun]:
    return [JobRun.from_dict(d) for d in json.loads(Path(path).read_text(encoding="utf-8"))]


def save_file(runs: list[JobRun], path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps([r.to_dict() for r in runs], indent=1), encoding="utf-8")


def _customer_and_workflow(path: str) -> tuple[str, str] | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "customers":
        return parts[1], parts[-1]
    return None


class JS7Client:
    def __init__(self, url: str, user: str, password: str, controller_id: str = "controller"):
        self.url = url.rstrip("/")
        self.controller_id = controller_id
        self.session = requests.Session()
        resp = self.session.post(f"{self.url}/joc/api/authentication/login",
                                 auth=(user, password), timeout=30)
        resp.raise_for_status()
        token = resp.headers.get("X-Access-Token") or resp.json().get("accessToken")
        self.session.headers["X-Access-Token"] = token

    @classmethod
    def from_env(cls) -> "JS7Client | None":
        url = os.environ.get("JS7_URL")
        if not url:
            return None
        return cls(url, os.environ.get("JS7_USER", "root"), os.environ.get("JS7_PASSWORD", "root"),
                   os.environ.get("JS7_CONTROLLER_ID", "controller"))

    def _order_history(self, date_from: str) -> list[dict]:
        resp = self.session.post(f"{self.url}/joc/api/orders/history", timeout=60, json={
            "controllerId": self.controller_id, "dateFrom": date_from, "limit": 50000})
        resp.raise_for_status()
        return resp.json().get("history", [])

    def history(self, days: int = 60) -> list[JobRun]:
        runs = []
        for item in self._order_history(f"-{days}d"):
            owner = _customer_and_workflow(item.get("workflow", ""))
            if not owner or not item.get("startTime") or not item.get("endTime"):
                continue
            runs.append(JobRun(owner[0], owner[1], parse_utc(item["startTime"]), parse_utc(item["endTime"])))
        return sorted(runs, key=lambda r: r.start)

    def running(self, customer: str) -> list[str]:
        """Workflows of this customer that have started but not finished.
        Patching while one of these runs is exactly what the planner exists to avoid."""
        names = []
        for item in self._order_history("-1d"):
            owner = _customer_and_workflow(item.get("workflow", ""))
            if owner and owner[0] == customer and item.get("startTime") and not item.get("endTime"):
                names.append(owner[1])
        return sorted(set(names))
