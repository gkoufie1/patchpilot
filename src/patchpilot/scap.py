"""OpenSCAP STIG scans before and after patching, compared rule by rule.

ACAS/Nessus is the scanner of record on the real platform; OpenSCAP with the
SCAP Security Guide's RHEL 9 STIG profile is the free equivalent for the
configuration side, and it ships in RHEL's own repos. We parse oscap's stdout
(Title/Rule/Result blocks) instead of pulling multi-megabyte result XML back
through Salt.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .lab import Lab

DATASTREAM = "/usr/share/xml/scap/ssg/content/ssg-rhel9-ds.xml"
PROFILE = "xccdf_org.ssgproject.content_profile_stig"


def scan_command(tag: str) -> str:
    # Lowest CPU and I/O priority: the scan shares the host with the database.
    return (f"mkdir -p /var/tmp/patchpilot && nice -n 19 ionice -c 3 oscap xccdf eval --profile {PROFILE} "
            f"--report /var/tmp/patchpilot/stig-{tag}.html {DATASTREAM}")


def parse_results(stdout: str) -> dict[str, str]:
    results, rule = {}, None
    # Split on \n only: oscap writes "Rule\r\t<id>", and splitlines() would treat
    # that \r as a line break, separating every key from its value.
    for line in stdout.split("\n"):
        key, _, value = line.replace("\r", "").partition("\t")
        if key.strip() == "Rule":
            rule = value.strip()
        elif key.strip() == "Result" and rule:
            results[rule] = value.strip()
            rule = None
    return results


@dataclass
class ScanDiff:
    minion: str
    before: dict[str, str]
    after: dict[str, str]
    fixed: list[str] = field(default_factory=list)
    regressed: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        def fails(d): return sum(1 for v in d.values() if v == "fail")
        return (f"{self.minion}: {fails(self.before)} -> {fails(self.after)} failing rules "
                f"({len(self.fixed)} fixed, {len(self.regressed)} new)")


def diff(minion: str, before: dict[str, str], after: dict[str, str]) -> ScanDiff:
    d = ScanDiff(minion, before, after)
    for rule, res in sorted(after.items()):
        was = before.get(rule)
        if was == "fail" and res == "pass":
            d.fixed.append(rule)
        elif was == "pass" and res == "fail":
            d.regressed.append(rule)
    return d


def scan(lab: Lab, minion: str, tag: str) -> dict[str, str] | None:
    """Rule -> result, or None when the host returned nothing usable.

    None matters: a host that never answered must not look like a clean scan.
    oscap exits 2 when any rule fails; that is a normal result, not an error.
    """
    ret = lab.salt.cmd(minion, "cmd.run_all", scan_command(tag), timeout=1800).get(minion)
    if not isinstance(ret, dict):   # missing, or "Minion did not return. [No response]"
        return None
    return parse_results(ret.get("stdout", "")) or None
