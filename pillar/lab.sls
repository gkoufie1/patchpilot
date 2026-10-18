lab:
  # How the lab starts. Change to enforcing once the app policy is in the baseline.
  selinux_mode: permissive
  # Lab-only database password; the VPC has no inbound access from the internet.
  # Override it on the master in /srv/patchpilot/pillar/lab.sls if you like.
  oracle_password: PatchPilotLab_2026
  # Swap file size on Linux nodes (salt/common/swap.sls).
  swap_mb: 2048

tomcat:
  port: 8090
