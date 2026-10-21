@echo off
rem st-sync.cmd : sync .st files in a folder with a CODESYS project
rem   st-sync.cmd export "<project>.project" "<ST folder>"   (project -> ST folder\*.st)
rem   st-sync.cmd import "<project>.project" "<ST folder>"   (ST folder\*.st -> project, then save)
rem Close the project in the CODESYS IDE before running.
setlocal

rem ===== edit these 2 lines for your environment =====
set "CODESYS_EXE=C:\Program Files\CODESYS 3.5.22.40\CODESYS\Common\CODESYS.exe"
set "CODESYS_PROFILE=CODESYS V3.5 SP22 Patch 4"
rem ====================================================

if "%~3"=="" goto usage
if /i not "%~1"=="import" if /i not "%~1"=="export" goto usage
if not exist "%CODESYS_EXE%" (
  echo CODESYS.exe not found: %CODESYS_EXE%
  exit /b 1
)
if not exist "%~2" (
  echo project not found: %~2
  exit /b 1
)
if /i "%~1"=="import" if not exist "%~3\" (
  echo ST folder not found: %~3
  exit /b 1
)

set "ST_SYNC_MODE=%~1"
set "ST_SYNC_PROJECT=%~f2"
set "ST_SYNC_DIR=%~f3"

echo running %ST_SYNC_MODE% ...
echo   project : %ST_SYNC_PROJECT%
echo   ST dir  : %ST_SYNC_DIR%
"%CODESYS_EXE%" --profile="%CODESYS_PROFILE%" --runscript="%~dp0st_sync.py" --noUI
echo.
type "%~dp0st_sync.log"
exit /b 0

:usage
echo usage: st-sync.cmd import^|export "<project>.project" "<ST folder>"
echo   e.g. st-sync.cmd export "C:\work\codesys-elevator\codesys\Elevator.project" "C:\work\codesys-elevator\codesys\src"
exit /b 1
