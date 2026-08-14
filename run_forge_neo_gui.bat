@echo off
setlocal

cd /d "%~dp0"
set "SD_WEBUI_URL=http://127.0.0.1:7861"
set "SD_WEBUI_PAYLOAD=%~dp0examples\payload_forge_neo.json"
set "SD_WEBUI_DYNAMIC_PROMPTS=1"

if exist "C:\[wildcards]\wildcards" (
    set "SD_WEBUI_WILDCARDS=C:\[wildcards]\wildcards"
) else (
    set "SD_WEBUI_WILDCARDS=Z:\StabilityMatrix\Data\Packages\Stable Diffusion WebUI Forge - Classic\extensions\sd-dynamic-prompts\wildcards"
)

if exist "Z:\AI-Throughput-Production" (
    set "SD_WEBUI_MANIFEST_DIR=Z:\AI-Throughput-Production\manifests"
) else (
    set "SD_WEBUI_MANIFEST_DIR=%USERPROFILE%\Documents\AI-Throughput-Production\manifests"
)

python -m sd_webui_batch.gui
if errorlevel 1 (
    echo.
    echo GUI exited with an error.
    pause
)
