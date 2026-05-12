@echo off
setlocal

cd /d "%~dp0"

python -m sd_webui_batch.gui
if errorlevel 1 (
    echo.
    echo GUI exited with an error.
    pause
)
