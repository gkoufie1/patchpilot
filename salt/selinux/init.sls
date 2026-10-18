# Lab baseline: SELinux as many long-lived environments have it, Permissive.
# Permissive hides a missing policy (nginx -> Tomcat) until something flips the
# mode back to Enforcing, which is the failure this lab reproduces.
{% set mode = salt['pillar.get']('lab:selinux_mode', 'permissive') %}
selinux-mode:
  selinux.mode:
    - name: {{ mode }}
