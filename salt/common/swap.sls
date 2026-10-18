# A swap file. RHEL cloud images ship with none, so when memory runs out
# the kernel thrashes its file cache and the whole host can freeze without
# logging anything (seen in the lab: Oracle + a STIG scan on 4 GB, no swap).
# With swap, the same spike makes the host slow instead of unreachable.
{% set size_mb = salt['pillar.get']('lab:swap_mb', 2048) %}

swapfile:
  cmd.run:
    - name: |
        dd if=/dev/zero of=/swapfile bs=1M count={{ size_mb }} status=none
        chmod 600 /swapfile
        mkswap /swapfile
    - creates: /swapfile

swapfile-active:
  mount.swap:
    - name: /swapfile
    - persist: True
    - require:
      - cmd: swapfile

# Prefer keeping the database in RAM; use swap only under real pressure.
vm.swappiness:
  sysctl.present:
    - value: 10
