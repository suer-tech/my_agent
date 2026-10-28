$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path $projectRoot 'data\iva-runtime.json')) {
    $runtime = Get-Content -LiteralPath (Join-Path $projectRoot 'data\iva-runtime.json') -Raw | ConvertFrom-Json
    if ($runtime.mode -eq 'telegram') { & (Join-Path $PSScriptRoot 'Stop-Iva.ps1'); exit $LASTEXITCODE }
}
$pidPath = Join-Path $projectRoot 'data/local-bot.pid'
if (-not (Test-Path -LiteralPath $pidPath)) { Write-Host 'Запись фонового процесса не найдена'; exit }
$record = Get-Content -LiteralPath $pidPath -Raw | ConvertFrom-Json
$workerProcess = Get-Process -Id $record.id -ErrorAction SilentlyContinue
if ($workerProcess) {
    if ($workerProcess.StartTime.ToUniversalTime().ToString('o') -ne $record.started) { throw 'PID уже принадлежит другому процессу. Остановка отклонена.' }
    & taskkill /PID $record.id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось остановить дерево процесса' }
}
Remove-Item -LiteralPath $pidPath
Write-Host 'Фоновый бот остановлен. Неоконченные операции при следующем запуске потребуют сверки.'
