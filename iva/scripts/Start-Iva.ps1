param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& python (Join-Path $projectRoot 'iva_integration\telegram_runtime.py') start
exit $LASTEXITCODE
