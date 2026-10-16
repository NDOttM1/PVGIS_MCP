@echo off
REM Einmalige Einrichtung: virtuelle Umgebung anlegen und PVGIS-MCP installieren
cd /d "%~dp0"
python -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :error
".venv\Scripts\pip.exe" install -e .[dev] || goto :error
".venv\Scripts\python.exe" -m pytest -q || goto :error
echo.
echo Installation erfolgreich. Befehl fuer Claude Desktop:
echo   %~dp0.venv\Scripts\pvgis-mcp.exe
pause
exit /b 0
:error
echo Installation fehlgeschlagen.
pause
exit /b 1
