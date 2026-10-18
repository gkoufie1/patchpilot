# The versions R&D certified. Salt pins these; PatchPilot validates against them.
# Plain YAML (no Jinja) so the Python side can read the same file.
certified:
  java_package: java-17-openjdk-headless
  tomcat_version: "9.0.98"
  # Lab only: the "someone upgraded Tomcat" scenario moves to this version.
  # Tomcat 10+ is Jakarta EE (jakarta.*), so javax.* code stops compiling.
  lab_unvetted_tomcat: "10.1.34"
  versionlock:
    - java-17-openjdk-headless
