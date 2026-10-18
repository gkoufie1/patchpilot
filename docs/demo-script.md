# Demo script (5 minutes)

A walkthrough for showing PatchPilot live or recording it. Every step works
in simulation on a laptop; on the AWS lab, drop `--simulate` and prefix
commands with `sudo`.

## 1. The problem (30 s)

Open `config/customers.yaml`. Three customers in three time zones, each with
non-prod and prod. The patch has to fit between their batch runs, and prod
comes after non-prod.

## 2. Plan the cycle (1 min)

```bash
patchpilot demo-data --as-of 2026-10-17
patchpilot plan --cycle-start 2026-10-19
```

Point out:
- globex prod starts at **03:47 Saturday**, because the Friday nightly ETL
  runs past 03:00 and there's a 45-minute buffer.
- Every prod window is on a weekend, at least 3 days after non-prod, and never
  in the month-end blackout.
- **Drift:** initech's nightly ETL grew 52% in two weeks (a new warehouse
  came online). The planner already used the longer runtimes. The flag tells
  you to confirm with the customer.
- Optional: `patchpilot plan --ai` adds a written summary for the change review.

## 3. A clean window (1 min)

```bash
patchpilot run --customer globex-industrial --env prod --simulate --ignore-window --scan
```

Walk through `runs/<id>/report.md`: the gates, the snapshot, pre-checks, the
patch order (DB, then app, then Windows), reboots only where
`needs-restarting` says so, 23 checks, and the STIG before/after comparison.

## 4. "A patch turned SELinux back on" (1 min)

```bash
patchpilot run --customer globex-industrial --env prod --simulate --ignore-window --inject selinux
```

Validation fails with 502s and AVC denials. The fix is
`httpd_can_network_connect`, and SELinux stays Enforcing. The follow-up
recommends making Enforcing the baseline.

## 5. "The graphs stopped painting after a Tomcat upgrade" (1 min)

```bash
patchpilot run --customer globex-industrial --env prod --simulate --ignore-window --inject tomcat
```

Login still works and only the chart fails, because Tomcat 10 is Jakarta EE
and the page uses `javax.*`. The version check spots the drift and Tomcat is
re-pinned. Then mention the case with no fix (`pytest -k unfixable`): if
Tomcat is certified and the chart still breaks, the run fails and recommends
an R&D defect.

## 6. Close (30 s)

The same commands run against real RHEL 9 and Windows Server nodes with Salt
(`infra/`). Next steps would be automating the pre-window customer
confirmation and feeding the vulnerability report into `patch_scope`.
