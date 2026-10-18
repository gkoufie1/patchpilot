# Windows Update through the Windows Update Agent. PatchPilot handles the reboot.
# pillar: patch_scope = "security" (default) or "all"
{% set scope = salt['pillar.get']('patch_scope', 'security') %}
windows-updates:
  wua.uptodate:
    - skip_reboot: True
{% if scope == 'security' %}
    - categories:
      - Security Updates
      - Critical Updates
{% endif %}
