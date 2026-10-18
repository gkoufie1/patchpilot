<%@ page contentType="text/html; charset=UTF-8" %>
<%@ page import="javax.servlet.http.HttpServletResponse" %>
<%--
  A "legacy" reporting screen written against the Java EE servlet API (javax.*).
  Tomcat 10 and later implement Jakarta EE (jakarta.*), so this page no longer
  compiles there and the chart stops rendering while the login page still works.
  That is the "graphs stopped painting after a Tomcat upgrade" failure.
--%>
<%
  response.setStatus(HttpServletResponse.SC_OK);
  String[] parts = {"PN-1001 Hydraulic pump", "PN-2040 Actuator seal", "PN-3310 Fuel valve",
                    "PN-4120 Bearing kit", "PN-5007 Control board"};
  int[] forecast = {42, 77, 23, 58, 35};
  int max = 0;
  for (int f : forecast) max = Math.max(max, f);
%>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Demand forecast (demo)</title>
  <style>body { font-family: system-ui, sans-serif; margin: 2rem; } text { font-size: 12px; }</style>
</head>
<body>
  <h1>90-day demand forecast by part</h1>
  <svg role="img" aria-label="Forecast demand per part" width="560" height="<%= parts.length * 36 + 10 %>">
<% for (int i = 0; i < parts.length; i++) {
     int w = forecast[i] * 360 / max; %>
    <text x="0" y="<%= i * 36 + 22 %>"><%= parts[i] %></text>
    <rect x="170" y="<%= i * 36 + 8 %>" width="<%= w %>" height="22" fill="#1f5fa8"></rect>
    <text x="<%= 176 + w %>" y="<%= i * 36 + 24 %>"><%= forecast[i] %></text>
<% } %>
  </svg>
  <p><small>Server: <%= application.getServerInfo() %></small></p>
</body>
</html>
