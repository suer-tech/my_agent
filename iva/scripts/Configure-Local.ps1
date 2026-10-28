$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot '.env'
if (Test-Path -LiteralPath $envPath) { throw '.env уже существует. Измените его локально; скрипт не перезаписывает секреты.' }
$secretToken = Read-Host 'Новый Telegram bot token (ввод скрыт)' -AsSecureString
$tokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secretToken)
try {
    $tokenText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPointer)
    if ($tokenText -notmatch '^\d+:[A-Za-z0-9_-]+$') { throw 'Неверный формат токена' }
    $settingsText = "TELEGRAM_BOT_TOKEN=$tokenText`nFINANCE_ALLOWED_USER_ID=412850740`nFINANCE_CODEX_TIMEOUT=150`nFINANCE_REMINDER_HOUR=9`nFINANCE_DEFAULT_LEAD_DAYS=1`nFINANCE_VOICE_MODE=auto`n"
    [IO.File]::WriteAllText($envPath, $settingsText, [Text.UTF8Encoding]::new($false))
    $userIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $envPath /inheritance:r /grant:r "${userIdentity}:(F)" /grant:r 'SYSTEM:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить ACL .env' }
    Write-Host 'Секрет сохранён локально с ограниченным ACL. Разрешён только ID 412850740.'
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPointer)
    $tokenText = $null
    $settingsText = $null
}
