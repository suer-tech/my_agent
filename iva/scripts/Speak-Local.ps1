param([Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
$spokenText = [Console]::In.ReadToEnd()
if ([string]::IsNullOrWhiteSpace($spokenText) -or $spokenText.Length -gt 4000) { throw 'Текст: 1..4000 символов' }
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $ruVoice = $synth.GetInstalledVoices() | Select-Object -ExpandProperty VoiceInfo | Where-Object { $_.Culture.Name -eq 'ru-RU' } | Select-Object -First 1
    if (-not $ruVoice) { throw 'Русский голос Windows не установлен' }
    $synth.SelectVoice($ruVoice.Name)
    $synth.SetOutputToWaveFile([IO.Path]::GetFullPath($Output))
    $synth.Speak($spokenText)
} finally { $synth.Dispose() }
