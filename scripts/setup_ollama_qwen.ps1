$ErrorActionPreference = "Stop"
Write-Host "Checking Ollama..."
ollama --version
Write-Host "Pulling local Qwen3-VL 8B Instruct..."
ollama pull qwen3-vl:8b-instruct
Write-Host "Testing model..."
ollama run qwen3-vl:8b-instruct "Reply with exactly: SCAG QWEN3-VL ONLINE"
Write-Host "Qwen3-VL setup complete."
