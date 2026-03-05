@echo off
setlocal

rem Workspace Brain (FULL) 소스 실행 헬퍼
rem - config/data를 레포 밖(D:\WB_Data)으로 분리
rem - GUI 실행 또는 전체 인덱싱 파이프라인(리셋→스캔→FTS→벡터→체인) 실행

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

rem 최초 1회: 설정 파일이 없으면 레포 기본값을 복사
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
echo   - 필요하면 settings.json에서 프로젝트(폴더)들을 추가/수정하세요.
exit /b 1

