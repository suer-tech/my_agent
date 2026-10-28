param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
& python (Join-Path $projectRoot 'iva_integration\telegram_runtime.py') status
exit $LASTEXITCODE
