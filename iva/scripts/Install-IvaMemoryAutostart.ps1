param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = (& python -c "import sys; print(sys.executable)").Trim()
$pythonwExe = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwExe)) { throw 'pythonw.exe is unavailable; hidden autostart was not installed' }
$scheduler = Join-Path $projectRoot 'iva_integration\memory_scheduler.py'
$startupDir = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupDir 'Iva Memory Pilot.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$arguments = '"' + $scheduler + '" loop'
if ((Test-Path -LiteralPath $shortcutPath) -and (($shortcut.TargetPath -ne $pythonwExe) -or ($shortcut.Arguments -ne $arguments))) { throw 'An unrelated startup entry exists; it was not changed' }
$shortcut.TargetPath = $pythonwExe
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Iva pilot native memory at 04:00 Asia/Yekaterinburg; no Telegram poller'
$shortcut.Save()
@{kind='current-user-login-shortcut';path=$shortcutPath;target=$pythonwExe;hidden=$true;requires_logged_in_windows=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'data\iva-pilot\memory-autostart.json') -Encoding UTF8
Write-Output 'Hidden memory autostart installed for the current Windows user; no system service changed'
