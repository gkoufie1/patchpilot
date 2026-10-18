# LAB ONLY. Reproduces "a patch turned SELinux back on": Enforcing at runtime and
# in /etc/selinux/config, so it survives the next reboot too.
selinux-enforcing:
  selinux.mode:
    - name: enforcing
