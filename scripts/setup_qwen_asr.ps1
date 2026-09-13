$ErrorActionPreference = "Stop"
Write-Host "SCAG Qwen3-ASR setup" -ForegroundColor Cyan
Write-Host "Qwen recommends Python 3.12 for qwen-asr. Checking launchers..."
$py312 = Get-Command py -ErrorAction SilentlyContinue
if ($py312) {
    try { & py -3.12 --version } catch { Write-Warning "Python 3.12 is not installed. Your existing venv may still work, but Qwen recommends 3.12." }
}
Write-Host "Installing qwen-asr into the active SCAG environment..."
python -m pip install -U qwen-asr
Write-Host "Done. First SCAG run will download Qwen3-ASR-1.7B and the 0.6B ForcedAligner automatically." -ForegroundColor Green
