<%@ page contentType="text/html; charset=UTF-8" %>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Service Parts Planning (demo)</title>
  <style>
    body { font-family: system-ui, sans-serif; background: #f4f6f8; display: grid; place-items: center; min-height: 100vh; margin: 0; }
    form { background: #fff; padding: 2rem; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,.12); width: 18rem; }
    label, input, button { display: block; width: 100%; box-sizing: border-box; }
    input { margin: .25rem 0 1rem; padding: .5rem; }
    button { padding: .6rem; background: #1f5fa8; color: #fff; border: 0; border-radius: 4px; }
    small { color: #667; }
  </style>
</head>
<body>
  <form action="chart.jsp" method="get">
    <h1>Sign in</h1>
    <label for="u">User</label><input id="u" name="u" autocomplete="username">
    <label for="p">Password</label><input id="p" name="p" type="password" autocomplete="current-password">
    <button type="submit">Sign in</button>
    <p><small>Demo application for the PatchPilot lab. Server: <%= application.getServerInfo() %></small></p>
  </form>
</body>
</html>
