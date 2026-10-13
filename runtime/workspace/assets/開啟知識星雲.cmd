@echo off
chcp 65001 >nul
where pythonw.exe >nul 2>nul
if errorlevel 1 (
  echo Python runtime unavailable. Please ask your workspace assistant to repair it.
  pause
  exit /b 1
)
if "%~1"=="--no-browser" (
  start "" /b pythonw.exe "%~dp0..\tools\knowledge_nebula_server.py" --root "%~dp0.." --no-browser --page nebula --require-generation knowledge-nebula-server-v2
) else (
  start "" /b pythonw.exe "%~dp0..\tools\knowledge_nebula_server.py" --root "%~dp0.." --page nebula --require-generation knowledge-nebula-server-v2
)
