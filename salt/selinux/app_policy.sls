# Remediation: let nginx (httpd_t) connect to Tomcat on its local port.
# This is the fix that keeps SELinux Enforcing; turning SELinux off would fail the STIG.
httpd-can-network-connect:
  selinux.boolean:
    - name: httpd_can_network_connect
    - value: True
    - persist: True
