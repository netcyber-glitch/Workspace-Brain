@echo off
setlocal

rem Workspace Brain (FULL) dev helper
rem - External storage root: D:\WB_Data
rem - Run: run_wb_full_dev.bat gui
rem - Run: run_wb_full_dev.bat pipeline

set "WB_ROOT=D:\WB_Data"
set "WORKSPACE_BRAIN_ROOT=%WB_ROOT%"

set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
  set "PY=%SCRIPT_DIR%.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

if not exist "%WB_ROOT%\config" mkdir "%WB_ROOT%\config" >nul 2>&1
if not exist "%WB_ROOT%\data" mkdir "%WB_ROOT%\data" >nul 2>&1

rem First run: copy default settings into WB_ROOT if missing
if not exist "%WB_ROOT%\config\settings.local.json" (
  if exist "%SCRIPT_DIR%config\settings.local.json" (
    copy /Y "%SCRIPT_DIR%config\settings.local.json" "%WB_ROOT%\config\settings.local.json" >nul
  )
)
if not exist "%WB_ROOT%\config\settings.json" (
  if exist "%SCRIPT_DIR%config\settings.json" (
    copy /Y "%SCRIPT_DIR%config\settings.json" "%WB_ROOT%\config\settings.json" >nul
  )
)

if "%~1"=="" goto :usage

if /I "%~1"=="gui" (
  "%PY%" "%SCRIPT_DIR%workspace_brain_gui.py" --root "%WB_ROOT%"
  exit /b %ERRORLEVEL%
)

if /I "%~1"=="pipeline" (
  "%PY%" "%SCRIPT_DIR%scan_all.py" --root "%WB_ROOT%" --reset-index --rebuild-fts --index-vectors --vector-include-large-text --build-version-chains
  exit /b %ERRORLEVEL%
)

:usage
echo Usage:
echo   %~nx0 gui
echo   %~nx0 pipeline
echo.
echo Notes:
echo   - storage root: %WB_ROOT%
echo   - Edit settings.json to add projects.
exit /b 1
