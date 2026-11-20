param([switch]$ListVoices)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$taskSpeaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    if ($ListVoices) {
        $taskCultures = @($taskSpeaker.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Culture.Name } | Sort-Object -Unique)
        ConvertTo-Json -InputObject $taskCultures -Compress
        exit 0
    }
    $taskVoiceInput = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $taskLanguage = [string]$taskVoiceInput.language
    $taskVoices = @($taskSpeaker.GetInstalledVoices() | Where-Object {
        $_.Enabled -and $(if ($taskLanguage -eq 'yue') { $_.VoiceInfo.Culture.Name -in @('zh-HK','zh-MO','yue-HK','yue-CN') }
        elseif ($taskLanguage -eq 'zh') { $_.VoiceInfo.Culture.Name -in @('zh-CN','zh-TW','zh-SG') }
        else { $_.VoiceInfo.Culture.TwoLetterISOLanguageName -eq $taskLanguage })
    })
    $taskVoice = $taskVoices | Where-Object { $_.VoiceInfo.Gender -eq 'Male' } | Select-Object -First 1
    if (-not $taskVoice) { $taskVoice = $taskVoices | Select-Object -First 1 }
    if (-not $taskVoice) { throw 'No installed voice for this language' }
    $taskSpeaker.SelectVoice($taskVoice.VoiceInfo.Name)
    $taskSpeaker.Rate = 0
    $taskSpeaker.SetOutputToWaveFile([string]$taskVoiceInput.output)
    $taskSpeaker.Speak([string]$taskVoiceInput.text)
    $taskSpeaker.SetOutputToNull()
    @{ voice = $taskVoice.VoiceInfo.Name; gender = [string]$taskVoice.VoiceInfo.Gender } | ConvertTo-Json -Compress
} finally { $taskSpeaker.Dispose() }
