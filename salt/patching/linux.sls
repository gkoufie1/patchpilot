# OS patching for RHEL. Certified packages (Java) are version-locked, so dnf
# skips them; Tomcat isn't an RPM at all. Reboots are handled by PatchPilot,
# which checks `needs-restarting -r` and waits for the minion to come back.
#
# pillar: patch_scope = "security" (default) or "all"
{% set scope = salt['pillar.get']('patch_scope', 'security') %}
{% set locks = salt['pillar.get']('certified:versionlock', []) %}

versionlock-plugin:
  pkg.installed:
    - name: python3-dnf-plugin-versionlock

{% if grains.get('role') == 'app' %}
{% for p in locks %}
versionlock-{{ p }}:
  cmd.run:
    - name: dnf versionlock add {{ p }}
    - unless: dnf versionlock list | grep -q '^{{ p }}-[0-9]'
    - require:
      - pkg: versionlock-plugin
{% endfor %}
{% endif %}

os-updates:
  cmd.run:
    - name: dnf -y upgrade {{ '--security' if scope == 'security' else '' }}
    - require:
      - pkg: versionlock-plugin
{% if grains.get('role') == 'app' %}
{% for p in locks %}
      - cmd: versionlock-{{ p }}
{% endfor %}
{% endif %}
