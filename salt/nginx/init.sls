# nginx in front of Tomcat. nginx runs confined as httpd_t, so under Enforcing
# SELinux it needs httpd_can_network_connect to reach Tomcat's port.
nginx:
  pkg.installed: []
  file.managed:
    - name: /etc/nginx/nginx.conf
    - source: salt://nginx/files/nginx.conf.j2
    - template: jinja
    - context:
        tomcat_port: {{ salt['pillar.get']('tomcat:port', 8090) }}
    - require:
      - pkg: nginx
  service.running:
    - enable: True
    - watch:
      - file: nginx
