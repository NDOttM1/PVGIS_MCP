@echo off
REM PVGIS-MCP als HTTP-Server starten (Streamable HTTP unter http://<host>:8765/mcp)
REM Token setzen, sobald der Server im Netzwerk erreichbar sein soll.
cd /d "%~dp0"
if "%PVGIS_MCP_TOKEN%"=="" set PVGIS_MCP_TOKEN=bitte-aendern
if "%PVGIS_MCP_HOST%"=="" set PVGIS_MCP_HOST=127.0.0.1
if "%PVGIS_MCP_PORT%"=="" set PVGIS_MCP_PORT=8765
".venv\Scripts\pvgis-mcp.exe" --transport http --host %PVGIS_MCP_HOST% --port %PVGIS_MCP_PORT% --token %PVGIS_MCP_TOKEN% --log-level INFO
pause
