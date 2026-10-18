# Tools the patch and validation runbook rely on, plus swap as a memory safety net.
include:
  - common.swap

common-packages:
  pkg.installed:
    - pkgs:
      - yum-utils                        # needs-restarting -r (dnf-utils is only an alias on RHEL 9)
      - python3-dnf-plugin-versionlock   # keep certified packages out of OS patching
      - policycoreutils-python-utils     # semanage, for SELinux troubleshooting
      - audit                            # ausearch for AVC denials
