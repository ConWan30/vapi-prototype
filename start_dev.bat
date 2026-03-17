@echo off
REM VAPI Developer Start Script
REM Starts bridge (port 8080) and frontend dev server (port 5173) in separate windows.
REM Prerequisites: Python venv activated, npm installed in frontend/
REM Usage: Double-click or run from project root in any terminal.

echo ============================================
echo  VAPI Developer Stack Launcher
echo ============================================
echo.

echo [1/2] Starting VAPI Bridge (port 8080)...
start "VAPI Bridge" cmd /k "cd /d %~dp0 && python -m bridge.vapi_bridge.main"

echo Waiting 3 seconds for bridge to initialize...
timeout /t 3 /nobreak >nul

echo [2/2] Starting VAPI Frontend Dev Server (port 5173)...
start "VAPI Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo ============================================
echo  Services started in separate windows.
echo.
echo  Bridge:    http://localhost:8080
echo  Dashboard: http://localhost:5173
echo  Snapshot:  http://localhost:8080/dashboard/snapshot
echo  WS records: ws://localhost:8080/ws/records
echo  WS frames:  ws://localhost:8080/ws/frames
echo.
echo  Operator API key is in bridge/.env (OPERATOR_API_KEY)
echo  and frontend/.env (VITE_BRIDGE_API_KEY).
echo ============================================
echo.
echo Press any key to close this launcher window...
pause >nul
