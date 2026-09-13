#!/usr/bin/env bash
set -e
echo "Checking Ollama..."
ollama --version
echo "Pulling local Qwen3-VL 8B Instruct..."
ollama pull qwen3-vl:8b-instruct
echo "Testing model..."
ollama run qwen3-vl:8b-instruct "Reply with exactly: SCAG QWEN3-VL ONLINE"
echo "Qwen3-VL setup complete."
