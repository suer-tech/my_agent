param([switch]$Foreground)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (Test-Path -LiteralPath (Join-Path $projectRoot 'data\iva-runtime.json')) {
    $runtime = Get-Content -LiteralPath (Join-Path $projectRoot 'data\iva-runtime.json') -Raw | ConvertFrom-Json
    if ($runtime.mode -eq 'telegram') { & (Join-Path $PSScriptRoot 'Start-Iva.ps1'); exit $LASTEXITCODE }
}
$pythonExe = (& python -c "import sys; print(sys.executable)").Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $pythonExe)) { throw 'Не найден настоящий исполняемый файл Python' }
$runtimeDir = Join-Path $projectRoot 'data'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$userIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $runtimeDir /inheritance:r /grant:r "${userIdentity}:(OI)(CI)(F)" /grant:r 'SYSTEM:(OI)(CI)(F)' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить ACL финансовых данных' }
if ($Foreground) { & $pythonExe -m local_finance.bot; exit $LASTEXITCODE }
$pidPath = Join-Path $runtimeDir 'local-bot.pid'
if (Test-Path -LiteralPath $pidPath) {
    $record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
    $existingProcess = Get-Process -Id $record.id -ErrorAction SilentlyContinue
    if ($existingProcess -and $existingProcess.StartTime.ToUniversalTime().ToString('o') -eq $record.started) { throw 'Локальный бот уже запущен' }
}
$outPath = Join-Path $runtimeDir 'local-bot.stdout.log'
$errPath = Join-Path $runtimeDir 'local-bot.stderr.log'
$workerProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','local_finance.bot') -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath -PassThru
Start-Sleep -Seconds 2
if ($workerProcess.HasExited) { throw 'Запуск остановлен. Проверьте .env и data/local-bot.stderr.log' }
@{id=$workerProcess.Id;started=$workerProcess.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $pidPath
Write-Host 'Бот запущен в фоне. Остановка: scripts/Stop-Local.ps1'
