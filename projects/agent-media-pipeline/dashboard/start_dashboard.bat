@echo off
rem Starts the Control Room dashboard and opens it in the browser (default port; see DASHBOARD_PORT in config.py).
start "" http://localhost:8765
python "%~dp0server.py"
