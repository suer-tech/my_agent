param()
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = (& python -c "import sys; print(sys.executable)").Trim()
$pythonwExe = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwExe)) { throw 'pythonw.exe unavailable' }
$path = Join-Path ([Environment]::GetFolderPath('Startup')) 'Iva Telegram.lnk'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($path)
$arguments = '"' + (Join-Path $projectRoot 'iva_integration\telegram_runtime.py') + '" supervise'
if ((Test-Path -LiteralPath $path) -and (($shortcut.TargetPath -ne $pythonwExe) -or ($shortcut.Arguments -ne $arguments))) { throw 'Unrelated startup entry was not overwritten' }
$shortcut.TargetPath = $pythonwExe
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'Real Iva Telegram transport; one authoritative financial ledger'
$shortcut.Save()
@{path=$path;kind='current-user-login';hidden=$true;requires_logged_in_windows=$true} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'data\iva-telegram\autostart.json') -Encoding UTF8
Write-Output 'Hidden Iva Telegram autostart installed'
