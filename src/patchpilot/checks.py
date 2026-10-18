"""Pre-patch snapshots and the post-patch validation runbook.

The checks are the manual runbook made executable: is it up, can you log in,
do the screens render, did anything we pin (Java, Tomcat) or rely on (SELinux
policy, services) change underneath us. Results are pass / warn / fail: a
warning is something a human should read, a failure blocks sign-off and
triggers remediation.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from .lab import Lab

APP_CONTEXT = "/patchpilot-demo"
TOMCAT_DIR_RE = re.compile(r"apache-tomcat-([\d.]+)")


@dataclass
class CheckResult:
    id: str
    target: str
    status: str   # "pass" | "warn" | "fail"
    detail: str

    @property
    def failed(self) -> bool:
        return self.status == "fail"


@dataclass
class Snapshot:
    minion: str
    role: str
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _one(lab: Lab, minion: str, fun: str, *args, **kwargs):
    return lab.salt.cmd(minion, fun, *args, **kwargs).get(minion)


def _sebool(lab: Lab, minion: str, name: str) -> str:
    ret = _one(lab, minion, "selinux.getsebool", name) or {}
    return str(ret.get(name, {}).get("State", "unknown"))


def _tomcat_version(lab: Lab, minion: str) -> str:
    out = str(_one(lab, minion, "cmd.run", "readlink -f /opt/tomcat/current") or "")
    m = TOMCAT_DIR_RE.search(out)
    return m.group(1) if m else "unknown"


def take_snapshot(lab: Lab, minion: str, role: str, certified: dict) -> Snapshot:
    snap = Snapshot(minion, role)
    if role == "win":
        snap.data["pending_reboot"] = bool(_one(lab, minion, "system.get_pending_reboot"))
        return snap
    snap.data["kernel"] = _one(lab, minion, "grains.get", "kernelrelease")
    snap.data["selinux"] = _one(lab, minion, "selinux.getenforce")
    snap.data["dnf_txn"] = str(_one(lab, minion, "cmd.run",
                                    "dnf history info last | awk -F': *' '/Transaction ID/ {print $2}'")).strip()
    if role == "app":
        snap.data["httpd_can_network_connect"] = _sebool(lab, minion, "httpd_can_network_connect")
        snap.data["tomcat"] = _tomcat_version(lab, minion)
        snap.data["java"] = str(_one(lab, minion, "cmd.run",
                                     f"rpm -q --qf '%{{VERSION}}-%{{RELEASE}}' {certified['java_package']}")).strip()
    return snap


class Validator:
    def __init__(self, lab: Lab, certified: dict, baseline: dict[str, Snapshot]):
        self.lab = lab
        self.certified = certified
        self.baseline = baseline

    def run(self, targets: dict[str, str]) -> list[CheckResult]:
        results: list[CheckResult] = []
        for role, minion in targets.items():
            if not self.lab.salt.ping(minion).get(minion):
                results.append(CheckResult("minion_up", minion, "fail", "minion did not answer test.ping"))
                continue
            results.append(CheckResult("minion_up", minion, "pass", "responding"))
            if role == "win":
                results += self._windows(minion)
                continue
            results += self._linux(minion)
            if role == "app":
                results += self._app(minion)
            elif role == "db":
                results += self._db(minion)
        return results

    # -- Linux, every role ---------------------------------------------------
    def _linux(self, m: str) -> list[CheckResult]:
        out = []
        usage = (_one(self.lab, m, "disk.usage") or {}).get("/", {})
        pct = int(str(usage.get("capacity", "0%")).rstrip("%") or 0)
        out.append(CheckResult("disk_root", m, "fail" if pct >= 90 else "warn" if pct >= 80 else "pass",
                               f"/ is {pct}% full"))
        # MemAvailable as % of MemTotal. A host near zero with no swap can freeze
        # solid instead of killing a process, and then nothing can reach it.
        mem = str(_one(self.lab, m, "cmd.run",
                       "awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} END{printf \"%d\", a*100/t}' /proc/meminfo")).strip()
        free = int(mem) if mem.isdigit() else 100
        out.append(CheckResult("memory_available", m, "fail" if free < 5 else "warn" if free < 15 else "pass",
                               f"{free}% of memory available"))
        reboot = _one(self.lab, m, "cmd.retcode", "needs-restarting -r")
        out.append(CheckResult("reboot_pending", m, "warn" if reboot == 1 else "pass",
                               "reboot still required" if reboot == 1 else "no reboot pending"))
        mode = _one(self.lab, m, "selinux.getenforce")
        before = self.baseline.get(m, Snapshot(m, "")).data.get("selinux")
        if before and mode != before:
            out.append(CheckResult("selinux_mode", m, "warn",
                                   f"SELinux changed from {before} to {mode} during patching"))
        else:
            out.append(CheckResult("selinux_mode", m, "pass", f"SELinux {mode}"))
        return out

    # -- App tier: Tomcat behind nginx ---------------------------------------
    def _app(self, m: str) -> list[CheckResult]:
        out = []
        want_tomcat = str(self.certified["tomcat_version"])
        have_tomcat = _tomcat_version(self.lab, m)
        out.append(CheckResult("tomcat_version", m, "pass" if have_tomcat == want_tomcat else "fail",
                               f"running {have_tomcat}, certified {want_tomcat}"))
        java_before = self.baseline.get(m, Snapshot(m, "")).data.get("java")
        java_now = str(_one(self.lab, m, "cmd.run",
                            f"rpm -q --qf '%{{VERSION}}-%{{RELEASE}}' {self.certified['java_package']}")).strip()
        out.append(CheckResult("java_pinned", m, "pass" if not java_before or java_now == java_before else "fail",
                               f"{self.certified['java_package']} {java_now}"
                               + ("" if not java_before or java_now == java_before else f" (was {java_before})")))
        for svc in ("tomcat", "nginx"):
            up = bool(_one(self.lab, m, "service.status", svc))
            out.append(CheckResult(f"service_{svc}", m, "pass" if up else "fail", "active" if up else "not running"))

        ip = self.lab.ip_of(m)
        for check_id, path, marker in (("http_login", "login.jsp", "Sign in"),
                                       ("http_chart", "chart.jsp", "<svg")):
            code, body = self.lab.http_get(f"http://{ip}{APP_CONTEXT}/{path}")
            ok = code == 200 and marker in body
            detail = f"HTTP {code}" + ("" if ok else f": {_first_line(body)}")
            out.append(CheckResult(check_id, m, "pass" if ok else "fail", detail))

        # After the HTTP checks, so any denial they caused is in the window.
        avc = str(_one(self.lab, m, "cmd.run",
                       "ausearch -m AVC -ts recent 2>/dev/null | grep -c 'type=AVC' || true")).strip()
        n = int(avc) if avc.isdigit() else 0
        out.append(CheckResult("selinux_denials", m, "warn" if n else "pass",
                               f"{n} AVC denials in the last 10 minutes" if n else "no recent AVC denials"))
        return out

    # -- Database tier ---------------------------------------------------------
    def _db(self, m: str) -> list[CheckResult]:
        healthy = _one(self.lab, m, "cmd.retcode", "podman exec oracle-free healthcheck.sh") == 0
        listener = self.lab.tcp_ok(self.lab.ip_of(m), 1521)
        return [
            CheckResult("oracle_health", m, "pass" if healthy else "fail",
                        "container healthcheck OK" if healthy else "healthcheck failed"),
            CheckResult("oracle_listener", m, "pass" if listener else "fail",
                        "port 1521 reachable from master" if listener else "port 1521 unreachable"),
        ]

    # -- Windows app server ------------------------------------------------------
    def _windows(self, m: str) -> list[CheckResult]:
        pending = bool(_one(self.lab, m, "system.get_pending_reboot"))
        iis = bool(_one(self.lab, m, "service.status", "W3SVC"))
        code, body = self.lab.http_get(f"http://{self.lab.ip_of(m)}/")
        return [
            CheckResult("reboot_pending", m, "warn" if pending else "pass",
                        "reboot still required" if pending else "no reboot pending"),
            CheckResult("service_w3svc", m, "pass" if iis else "fail", "IIS running" if iis else "IIS stopped"),
            CheckResult("http_iis", m, "pass" if code == 200 else "fail", f"HTTP {code}"),
        ]


def _first_line(text: str) -> str:
    """The most useful line of an error page: the exception if there is one."""
    lines = [ln.strip() for ln in re.sub(r"<[^>]+>", " ", text).splitlines() if ln.strip()]
    for ln in lines:
        if re.search(r"Unable to compile|Exception|cannot be resolved|denied", ln):
            return ln[:200]
    return lines[0][:200] if lines else ""


@dataclass
class Remediation:
    sls: str | None
    target: str
    why: str
    command: tuple | None = None   # (fun, *args) for non-state fixes


def plan_remediation(results: list[CheckResult]) -> tuple[list[Remediation], list[CheckResult]]:
    """Map failing checks to known fixes. Returns (fixes, failures nobody knows how to fix)."""
    by = {(r.id, r.target): r for r in results}
    fixes: list[Remediation] = []
    unknown: list[CheckResult] = []
    for r in results:
        if not r.failed:
            continue
        m = r.target
        if r.id == "tomcat_version":
            fixes.append(Remediation("tomcat", m, "Tomcat is not the R&D-certified version; re-pin it"))
        elif r.id in ("http_login", "http_chart") and "502" in r.detail and (
                by.get(("selinux_denials", m), r).status == "warn" or by.get(("selinux_mode", m), r).status == "warn"):
            fixes.append(Remediation("selinux.app_policy", m,
                                     "nginx is blocked by SELinux from reaching Tomcat; allow it with "
                                     "httpd_can_network_connect instead of turning SELinux off"))
        elif r.id == "http_chart" and by.get(("tomcat_version", m), r).failed:
            continue  # fixed by the Tomcat re-pin above
        elif r.id.startswith("service_") and r.id != "service_w3svc":
            fixes.append(Remediation(None, m, f"{r.id[8:]} is down; restart it", ("service.restart", r.id[8:])))
        elif r.id == "service_w3svc":
            fixes.append(Remediation(None, m, "IIS is down; restart it", ("service.restart", "W3SVC")))
        else:
            unknown.append(r)
    # One fix per (sls/command, target).
    seen, unique = set(), []
    for f in fixes:
        key = (f.sls, f.command, f.target)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, unknown
