"""ServiceNow change requests, chat notifications and S3 report storage.

Each integration is optional and configured from environment variables; when
one isn't configured it logs what it would have done and carries on, so a
missing webhook never blocks a patch window.

  SNOW_INSTANCE  SNOW_USER  SNOW_PASSWORD     ServiceNow (a free Personal Developer Instance works)
  PATCHPILOT_WEBHOOK_URL                      Teams Workflows or Slack incoming webhook
  PATCHPILOT_S3_BUCKET                        bucket for run reports (set by Terraform on the master)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

import requests

log = logging.getLogger("patchpilot")


class ServiceNow:
    """Change requests through the Table API (/api/now/table/change_request)."""

    def __init__(self, instance: str, user: str, password: str):
        self.base = instance.rstrip("/")
        if not self.base.startswith("http"):
            self.base = f"https://{self.base}.service-now.com"
        self.session = requests.Session()
        self.session.auth = (user, password)
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    @classmethod
    def from_env(cls) -> "ServiceNow | None":
        inst = os.environ.get("SNOW_INSTANCE")
        if not inst:
            return None
        return cls(inst, os.environ["SNOW_USER"], os.environ["SNOW_PASSWORD"])

    def create_change(self, short: str, description: str, start: datetime, end: datetime) -> dict:
        fmt = "%Y-%m-%d %H:%M:%S"   # Table API takes internal (UTC) values by default
        resp = self.session.post(f"{self.base}/api/now/table/change_request", timeout=30, json={
            "short_description": short, "description": description, "type": "normal",
            "category": "Software", "start_date": start.strftime(fmt), "end_date": end.strftime(fmt),
        })
        resp.raise_for_status()
        rec = resp.json()["result"]
        return {"sys_id": rec["sys_id"], "number": rec["number"]}

    def work_note(self, sys_id: str, note: str) -> None:
        self.session.patch(f"{self.base}/api/now/table/change_request/{sys_id}",
                           json={"work_notes": note}, timeout=30).raise_for_status()

    def close(self, sys_id: str, successful: bool, notes: str) -> None:
        # Change models may forbid jumping straight to Closed; record the outcome either way.
        body = {"close_code": "successful" if successful else "unsuccessful", "close_notes": notes, "state": "3"}
        resp = self.session.patch(f"{self.base}/api/now/table/change_request/{sys_id}", json=body, timeout=30)
        if resp.status_code >= 400:
            body.pop("state")
            self.session.patch(f"{self.base}/api/now/table/change_request/{sys_id}",
                               json=body, timeout=30).raise_for_status()


def notify(text: str, url: str | None = None) -> None:
    url = url or os.environ.get("PATCHPILOT_WEBHOOK_URL")
    if not url:
        log.info("notify (no webhook configured): %s", text.splitlines()[0])
        return
    if "hooks.slack.com" in url:
        payload = {"text": text}
    else:  # Teams Workflows "post to a channel when a webhook request is received"
        payload = {"type": "message", "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "content": {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "type": "AdaptiveCard", "version": "1.4",
                        "body": [{"type": "TextBlock", "text": text, "wrap": True}]},
        }]}
    try:
        requests.post(url, json=payload, timeout=15).raise_for_status()
    except requests.RequestException as exc:
        log.warning("webhook failed: %s", exc)


def upload_dir(local_dir: Path, prefix: str, bucket: str | None = None) -> list[str]:
    bucket = bucket or os.environ.get("PATCHPILOT_S3_BUCKET")
    if not bucket:
        log.info("S3 upload skipped (PATCHPILOT_S3_BUCKET not set)")
        return []
    import boto3  # optional dependency: pip install patchpilot[aws]

    s3 = boto3.client("s3")
    keys = []
    for f in sorted(local_dir.rglob("*")):
        if f.is_file():
            key = f"{prefix.rstrip('/')}/{f.relative_to(local_dir).as_posix()}"
            s3.upload_file(str(f), bucket, key)
            keys.append(f"s3://{bucket}/{key}")
    return keys
