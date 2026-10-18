# Stand-in for the Windows application tier: IIS with its default page.
iis:
  win_servermanager.installed:
    - name: Web-Server

w3svc:
  service.running:
    - name: W3SVC
    - enable: True
    - require:
      - win_servermanager: iis
