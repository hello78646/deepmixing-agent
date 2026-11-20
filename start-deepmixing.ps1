param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskPython = (Get-Command python -ErrorAction Stop).Source
}
$planetVoiceConfig = Join-Path $PSScriptRoot '.local\planet-voice.json'
if (Test-Path -LiteralPath $planetVoiceConfig) {
    try {
        if ((Get-Content -LiteralPath $planetVoiceConfig -Raw | ConvertFrom-Json).enabled -eq $true) {
            & (Join-Path $PSScriptRoot 'tools\start_planet_voice.ps1')
        }
    } catch { Write-Warning ("Planet voice did not start; text chat remains available. " + $_.Exception.Message) }
}
& $taskPython -m deepmixing_audio --port $Port
