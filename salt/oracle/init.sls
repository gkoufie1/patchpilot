# Oracle Database Free in a Podman container, run by systemd. The database
# itself is the DBA team's to patch; we patch the OS under it and prove it came back.
podman:
  pkg.installed: []

oracle-free-unit:
  file.managed:
    - name: /etc/systemd/system/oracle-free.service
    - source: salt://oracle/files/oracle-free.service.j2
    - template: jinja
    - mode: '0600'
    - context:
        password: {{ salt['pillar.get']('lab:oracle_password') }}

oracle-free:
  service.running:
    - enable: True
    - require:
      - pkg: podman
    - watch:
      - file: oracle-free-unit
