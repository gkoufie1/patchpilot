# Tomcat from the Apache archive, outside the OS package manager, pinned to the
# R&D-certified version. Each version gets its own directory and /opt/tomcat/current
# points at the active one, so going back is a symlink flip.
#
# Override the version with pillar (tomcat:version) only for the lab scenario.
{% set certified = salt['pillar.get']('certified:tomcat_version') %}
{% set ver = salt['pillar.get']('tomcat:version', certified) %}
{% set major = ver.split('.')[0] %}
{% set port = salt['pillar.get']('tomcat:port', 8090) %}
{% set home = '/opt/tomcat/apache-tomcat-' ~ ver %}
{% set java = salt['pillar.get']('certified:java_package') %}

java:
  pkg.installed:
    - name: {{ java }}

java-versionlock:
  cmd.run:
    - name: dnf versionlock add {{ java }}
    - unless: dnf versionlock list | grep -q '^{{ java }}-[0-9]'
    - require:
      - pkg: java

tomcat-user:
  user.present:
    - name: tomcat
    - system: True
    - home: /opt/tomcat
    - createhome: False
    - shell: /sbin/nologin

tomcat-dist:
  archive.extracted:
    - name: /opt/tomcat
    - source: https://archive.apache.org/dist/tomcat/tomcat-{{ major }}/v{{ ver }}/bin/apache-tomcat-{{ ver }}.tar.gz
    - source_hash: https://archive.apache.org/dist/tomcat/tomcat-{{ major }}/v{{ ver }}/bin/apache-tomcat-{{ ver }}.tar.gz.sha512
    - user: tomcat
    - group: tomcat
    - if_missing: {{ home }}
    - require:
      - user: tomcat-user

# Listen on loopback only; nginx is the front door.
tomcat-connector:
  file.replace:
    - name: {{ home }}/conf/server.xml
    - pattern: '<Connector port="8080"'
    - repl: '<Connector address="127.0.0.1" port="{{ port }}"'
    - require:
      - archive: tomcat-dist

demo-app-context:
  file.managed:
    - name: {{ home }}/conf/Catalina/localhost/patchpilot-demo.xml
    - contents: '<Context docBase="/opt/patchpilot/webapp" />'
    - makedirs: True
    - user: tomcat
    - group: tomcat
    - require:
      - archive: tomcat-dist

demo-app:
  file.recurse:
    - name: /opt/patchpilot/webapp
    - source: salt://webapp
    - user: tomcat
    - group: tomcat
    - clean: True

tomcat-current:
  file.symlink:
    - name: /opt/tomcat/current
    - target: {{ home }}
    - force: True
    - require:
      - file: tomcat-connector
      - file: demo-app-context

tomcat-unit:
  file.managed:
    - name: /etc/systemd/system/tomcat.service
    - source: salt://tomcat/files/tomcat.service

tomcat:
  service.running:
    - enable: True
    - require:
      - pkg: java
      - file: tomcat-unit
      - file: demo-app
    - watch:
      - file: tomcat-current
      - file: tomcat-unit
