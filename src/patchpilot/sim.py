"""An in-memory stand-in for the lab, so the whole cycle runs on a laptop.

It answers the same Salt functions PatchPilot calls and serves the same HTTP
responses the real lab would, including the two failure modes the lab is built
to reproduce:

* SELinux switched to Enforcing by a patch -> nginx can't reach Tomcat -> 502
* Tomcat moved to 10.x -> the javax.* chart page no longer compiles -> 500

Used by `--simulate` and by the tests. It is deliberately simple; it is not a
Salt emulator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from .lab import summarize_state


@dataclass
class SimNode:
    id: str
    role: str
    ip: str
    up: bool = True
    selinux: str = "Permissive"
    httpd_can_network_connect: bool = False
    tomcat: str = "9.0.98"
    java: str = "17.0.13.0.11-4.el9"
    kernel: str = "5.14.0-503.11.1.el9_5.x86_64"
    dnf_txn: int = 12
    pending_reboot: bool = False
    avc: int = 0
    disk_pct: int = 41
    mem_pct: int = 55
    services: dict = field(default_factory=dict)


class _SimSalt:
    def __init__(self, sim: "SimLab"):
        self.sim = sim

    def _nodes(self, target: str) -> list[SimNode]:
        if target in self.sim.nodes:
            return [self.sim.nodes[target]]
        prefix = target.rstrip("*")
        return [n for n in self.sim.nodes.values() if target.endswith("*") and n.id.startswith(prefix)]

    def cmd(self, target, fun, *args, timeout=None, **kwargs):
        self.sim.calls.append((target, fun, args, kwargs))
        return {n.id: self._one(n, fun, args, kwargs) for n in self._nodes(target) if n.up}

    def ping(self, target):
        return {n.id: True for n in self._nodes(target) if n.up}

    def state_apply(self, target, sls, pillar=None):
        return {m: summarize_state(r) for m, r in self.cmd(target, "state.apply", sls, pillar=pillar).items()}

    def reboot(self, target, windows=False):
        for n in self._nodes(target):
            n.pending_reboot = False
        self.sim.calls.append((target, "reboot", (), {}))

    def _one(self, n: SimNode, fun: str, args: tuple, kwargs: dict):
        a0 = args[0] if args else ""
        if fun == "test.ping":
            return True
        if fun == "selinux.getenforce":
            return n.selinux
        if fun == "selinux.getsebool":
            return {a0: {"State": "on" if n.httpd_can_network_connect else "off", "Default": "off"}}
        if fun == "grains.get":
            return {"kernelrelease": n.kernel}.get(a0, "")
        if fun == "network.ip_addrs":
            return [n.ip]
        if fun == "disk.usage":
            return {"/": {"capacity": f"{n.disk_pct}%"}}
        if fun == "system.get_pending_reboot":
            return n.pending_reboot
        if fun == "service.status":
            return n.services.get(a0, False)
        if fun in ("service.start", "service.restart"):
            n.services[a0] = True
            return True
        if fun == "service.stop":
            n.services[a0] = False
            return True
        if fun == "cmd.retcode":
            if a0.startswith("needs-restarting"):
                return 1 if n.pending_reboot else 0
            if "healthcheck" in a0:
                return 0 if n.services.get("oracle-free") else 1
            return 0
        if fun == "cmd.run":
            if a0.startswith("readlink"):
                return f"/opt/tomcat/apache-tomcat-{n.tomcat}"
            if a0.startswith("rpm -q"):
                return n.java
            if "Transaction ID" in a0:
                return str(n.dnf_txn)
            if "MemAvailable" in a0:
                return str(n.mem_pct)
            if a0.startswith("ausearch"):
                return str(n.avc)
            if "history rollback" in a0:
                n.dnf_txn += 1
                return "Complete!"
            return ""
        if fun == "cmd.run_all":
            return {"retcode": 2, "stdout": _oscap_stdout(n), "stderr": ""}
        if fun == "state.apply":
            return self._state(n, a0, kwargs.get("pillar") or {})
        return None

    def _state(self, n: SimNode, sls: str, pillar: dict) -> dict:
        ok = {"sim": {"result": True, "comment": f"{sls} applied", "__id__": sls}}
        if sls == "patching.linux":
            n.dnf_txn += 1
            n.kernel = "5.14.0-503.22.1.el9_5.x86_64"
            n.pending_reboot = True
            for hook in self.sim.after_patch:
                hook(n)
        elif sls == "patching.windows":
            n.pending_reboot = True
        elif sls == "tomcat":
            n.tomcat = str(pillar.get("tomcat", {}).get("version", self.sim.certified_tomcat))
            n.services["tomcat"] = True
        elif sls == "selinux.app_policy":
            n.httpd_can_network_connect = True
        elif sls == "break.selinux_reenabled":
            n.selinux = "Enforcing"
        return ok


def _oscap_stdout(n: SimNode) -> str:
    rules = {
        "xccdf_org.ssgproject.content_rule_selinux_state": n.selinux == "Enforcing",
        "xccdf_org.ssgproject.content_rule_security_patches_up_to_date": n.dnf_txn > 12,
        "xccdf_org.ssgproject.content_rule_package_telnet-server_removed": True,
        "xccdf_org.ssgproject.content_rule_sshd_disable_root_login": False,
    }
    return "\n".join(f"Title\t{r.rsplit('_rule_', 1)[1]}\nRule\t{r}\nResult\t{'pass' if ok else 'fail'}\n"
                     for r, ok in rules.items())


class SimLab:
    def __init__(self, certified_tomcat: str = "9.0.98"):
        self.certified_tomcat = certified_tomcat
        self.calls: list = []
        self.after_patch: list = []   # hooks that model side effects of patching
        self.nodes = {
            "globex-app01": SimNode("globex-app01", "app", "10.60.1.20",
                                    services={"tomcat": True, "nginx": True}),
            "globex-db01": SimNode("globex-db01", "db", "10.60.1.30", services={"oracle-free": True}),
            "globex-win01": SimNode("globex-win01", "win", "10.60.1.40", services={"W3SVC": True}),
        }
        self.salt = _SimSalt(self)

    # Network as seen from the master
    def _by_ip(self, host: str) -> SimNode | None:
        return next((n for n in self.nodes.values() if n.ip == host and n.up), None)

    def http_get(self, url: str, timeout: int = 15) -> tuple[int, str]:
        u = urlparse(url)
        n = self._by_ip(u.hostname or "")
        if n is None:
            return 0, "connection timed out"
        if n.role == "win":
            return (200, "<title>IIS Windows Server</title>") if n.services.get("W3SVC") else (0, "refused")
        if not n.services.get("nginx"):
            return 0, "connection refused"
        if u.path == "/healthz":
            return 200, "ok"
        if not n.services.get("tomcat"):
            return 502, "<h1>502 Bad Gateway</h1>"
        if n.selinux == "Enforcing" and not n.httpd_can_network_connect:
            n.avc += 1   # httpd_t denied name_connect to the Tomcat port
            return 502, "<h1>502 Bad Gateway</h1>"
        if u.path.endswith("login.jsp"):
            return 200, "<h1>Sign in</h1>"
        if u.path.endswith("chart.jsp"):
            if int(n.tomcat.split(".")[0]) >= 10:
                return 500, ("<h1>HTTP Status 500 - Internal Server Error</h1>\n"
                             "Unable to compile class for JSP: The import javax.servlet cannot be resolved")
            return 200, "<svg role='img'></svg>"
        return 404, "not found"

    def tcp_ok(self, host: str, port: int, timeout: int = 5) -> bool:
        n = self._by_ip(host)
        return bool(n and port == 1521 and n.services.get("oracle-free"))

    def ip_of(self, minion: str) -> str:
        return self.nodes[minion].ip

    def wait_up(self, minions, timeout=900, interval=15):
        return [m for m in minions if not self.nodes[m].up]

    def sleep(self, seconds: float) -> None:
        pass
