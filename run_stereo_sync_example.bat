@echo off
setlocal

set "SCRIPT_DIR=%~dp0"

if "%~2"=="" (
    echo Usage:
    echo   Drag and drop LEFT video onto this BAT, then RIGHT video, or run:
    echo   run_stereo_sync_example.bat left.mp4 right.mp4
    pause
    exit /b 1
)

set "LEFT=%~1"
set "RIGHT=%~2"
set "OUT=stacked_output.mkv"

REM Prefer .venv, then venv, both relative to this BAT file.
if exist "%SCRIPT_DIR%.env\Scripts\python.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%.env\Scripts\python.exe"
) else if exist "%SCRIPT_DIR%env\Scripts\python.exe" (
    set "PYTHON_EXE=%SCRIPT_DIR%env\Scripts\python.exe"
) else (
    echo ERROR: Could not find a virtual environment Python.
    echo Looked for:
    echo   "%SCRIPT_DIR%.env\Scripts\python.exe"
    echo   "%SCRIPT_DIR%env\Scripts\python.exe"
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT_DIR%stereo_sync_stack_v9.py" "%LEFT%" "%RIGHT%" --mode analyze
if errorlevel 1 (
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%SCRIPT_DIR%stereo_sync_stack_v9.py" "%LEFT%" "%RIGHT%" --mode render --output "%~dpn1_converted.mkv" --height 1080 --auto-align-vertical --auto-align-horizontal
pause