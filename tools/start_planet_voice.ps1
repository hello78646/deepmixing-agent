# Starts the accepted v2Pro voice on loopback only.
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$voiceRoot = Join-Path $taskRoot '.local\gpt-sovits'
$sourceRoot = Join-Path $voiceRoot 'source'
$voicePython = Join-Path $voiceRoot 'venv\Scripts\python.exe'
$voiceConfig = Join-Path $voiceRoot 'planet-infer.yaml'
$voiceEntry = Join-Path $taskRoot 'tools\serve_planet_voice.py'
foreach ($required in @($voicePython, $voiceConfig, $voiceEntry, (Join-Path $sourceRoot 'api_v2.py'), (Join-Path $sourceRoot 'runtime\open_jtalk_dic\sys.dic'))) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Missing voice runtime: $required" }
}
$listener = Get-NetTCPConnection -LocalPort 9880 -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener[0].OwningProcess)"
    $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($owner.ParentProcessId)" -ErrorAction SilentlyContinue
    $usesVoiceEnvironment = $owner.ExecutablePath -eq $voicePython -or $parent.ExecutablePath -eq $voicePython
    if ($listener.LocalAddress -ne '127.0.0.1' -or -not $usesVoiceEnvironment -or $owner.CommandLine.Replace('/', '\') -notlike ('*' + $voiceEntry + '*') -or $owner.CommandLine.Replace('/', '\') -notlike ('*' + $voiceConfig + '*')) {
        throw 'Port 9880 belongs to another service. Nothing was stopped or replaced.'
    }
    Invoke-RestMethod 'http://127.0.0.1:9880/openapi.json' -TimeoutSec 3 | Out-Null
    Write-Output 'Planet voice is already running at http://127.0.0.1:9880'
    return
}
# Leave headroom for the recorder and mixer; do not change synthesis or playback rate.
$logicalCpus = [Environment]::ProcessorCount
$voiceThreads = [string][Math]::Min([Math]::Min(8, $logicalCpus), [Math]::Max(4, [Math]::Floor($logicalCpus / 2)))
$voiceEnv = @{
    PYTHONPATH = "$sourceRoot\GPT_SoVITS;$sourceRoot"
    PATH = "$sourceRoot\runtime;$env:PATH"
    OMP_NUM_THREADS = $voiceThreads; MKL_NUM_THREADS = $voiceThreads
    HF_HUB_OFFLINE = '1'; TRANSFORMERS_OFFLINE = '1'; PYTHONUTF8 = '1'
    NLTK_DATA = "$sourceRoot\runtime\nltk_data"
    OPEN_JTALK_DICT_DIR = "$sourceRoot\runtime\open_jtalk_dic"
}
$previousEnv = @{}
try {
    foreach ($key in $voiceEnv.Keys) {
        $previousEnv[$key] = [Environment]::GetEnvironmentVariable($key, 'Process')
        [Environment]::SetEnvironmentVariable($key, $voiceEnv[$key], 'Process')
    }
    $voiceProcess = Start-Process -FilePath $voicePython -WorkingDirectory $sourceRoot -WindowStyle Hidden -PassThru `
        -ArgumentList @('-u', ('"' + $voiceEntry + '"'), '-a', '127.0.0.1', '-p', '9880', '-c', ('"' + $voiceConfig + '"')) `
        -RedirectStandardOutput (Join-Path $voiceRoot 'api.stdout.log') `
        -RedirectStandardError (Join-Path $voiceRoot 'api.stderr.log')
} finally {
    foreach ($key in $previousEnv.Keys) { [Environment]::SetEnvironmentVariable($key, $previousEnv[$key], 'Process') }
}
$deadline = [DateTime]::UtcNow.AddSeconds(45)
while ([DateTime]::UtcNow -lt $deadline) {
    $voiceProcess.Refresh()
    if ($voiceProcess.HasExited) { throw "Voice service exited. See $voiceRoot\api.stderr.log" }
    try {
        Invoke-RestMethod 'http://127.0.0.1:9880/openapi.json' -TimeoutSec 1 | Out-Null
        Write-Output 'Planet voice is ready at http://127.0.0.1:9880'
        return
    } catch { Start-Sleep -Milliseconds 500 }
}
throw "Voice startup is still pending. See $voiceRoot\api.stderr.log; do not start a second copy."
