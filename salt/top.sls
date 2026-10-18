# Highstate builds the lab. Patching, remediation and the lab "break" states are
# applied by name from PatchPilot, never from highstate.
base:
  'G@role:app':
    - match: compound
    - common
    - selinux
    - tomcat
    - nginx
    - scap
  'G@role:db':
    - match: compound
    - common
    - selinux
    - oracle
    - scap
  'G@role:win':
    - match: compound
    - iis
