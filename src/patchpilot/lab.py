"""Thin wrappers around the things PatchPilot touches: the Salt CLI and the network.

Everything goes through the `salt` command with JSON output rather than the
Python API, so the same commands can be pasted into a shell when debugging.
Run PatchPilot as root on the Salt master.
"""
from __future__ import annotations

import json
import socket
import subprocess
import time
from typing import Any

import requests


class SaltError(RuntimeError):
    pass


def summarize_state(ret: Any) -> tuple[bool, list[str]]:
    """state.apply returns {state_id: {result, comment, ...}} or a list of render errors."""
    if not isinstance(ret, dict):
        return False, [str(ret)]
    fails = [f"{v.get('__id__', k)}: {str(v.get('comment', '')).strip()[:300]}"
             for k, v in ret.items() if isinstance(v, dict) and v.get("result") is False]
    return not fails, fails


class SaltClient:
    def __init__(self, timeout: int = 300, binary: str = "salt"):
        self.timeout = timeout
        self.binary = binary

    def cmd(self, target: str, fun: str, *args: Any, timeout: int | None = None, **kwargs: Any) -> dict[str, Any]:
        t = timeout or self.timeout
        argv = [self.binary, "--out=json", "--static", f"--timeout={t}", target, fun, *map(str, args)]
        argv += [f"{k}={json.dumps(v) if isinstance(v, (dict, list, bool)) else v}" for k, v in kwargs.items()]
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=t + 60)
        out = proc.stdout.strip()
        if not out:
            raise SaltError(proc.stderr.strip() or f"salt returned nothing (exit {proc.returncode})")
        try:
            return json.loads(out)
        except json.JSONDecodeError as exc:
            raise SaltError(f"salt returned non-JSON output: {out[:300]}") from exc

    def ping(self, target: str) -> dict[str, bool]:
        try:
            return {k: v is True for k, v in self.cmd(target, "test.ping", timeout=20).items()}
        except SaltError:
            return {}

    def state_apply(self, target: str, sls: str, pillar: dict | None = None) -> dict[str, tuple[bool, list[str]]]:
        kwargs = {"pillar": pillar} if pillar else {}
        return {m: summarize_state(r) for m, r in self.cmd(target, "state.apply", sls, timeout=3600, **kwargs).items()}

    def reboot(self, target: str, windows: bool = False) -> None:
        # Fire-and-forget: a synchronous reboot call would lose its minion mid-return.
        self.cmd(target, "cmd.run_bg", "shutdown /r /t 10" if windows else "sleep 5 && systemctl reboot")


class Lab:
    """The real environment: Salt for the minions, HTTP/TCP from the master."""

    def __init__(self, salt: SaltClient | None = None):
        self.salt = salt or SaltClient()

    def http_get(self, url: str, timeout: int = 15) -> tuple[int, str]:
        try:
            r = requests.get(url, timeout=timeout)
            return r.status_code, r.text
        except requests.RequestException as exc:
            return 0, str(exc)

    def tcp_ok(self, host: str, port: int, timeout: int = 5) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def ip_of(self, minion: str) -> str:
        addrs = self.salt.cmd(minion, "network.ip_addrs", timeout=20).get(minion) or []
        if not addrs:
            raise SaltError(f"no IPv4 address reported by {minion}")
        return addrs[0]

    def wait_up(self, minions: list[str], timeout: int = 900, interval: int = 15) -> list[str]:
        """Wait until every minion answers test.ping; return the ones that never did."""
        deadline = time.monotonic() + timeout
        pending = set(minions)
        while pending and time.monotonic() < deadline:
            for m in list(pending):
                if self.salt.ping(m).get(m):
                    pending.discard(m)
            if pending:
                self.sleep(interval)
        return sorted(pending)
