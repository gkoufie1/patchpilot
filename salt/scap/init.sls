# OpenSCAP scanner plus the SCAP Security Guide content (RHEL 9 STIG profile).
openscap:
  pkg.installed:
    - pkgs:
      - openscap-scanner
      - scap-security-guide
