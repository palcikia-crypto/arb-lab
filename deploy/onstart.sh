#!/bin/bash
# deploy/onstart.sh — on-start script для инстанса Vast.ai.
# Поднимает OpenAI-совместимый vLLM-сервер. Модель задаётся переменной MODEL.
# В консоли Vast при запуске: Dock Image: vllm/vllm-openai:latest,
# On-start script: этот файл, порт пробросить как 8000 (http).
set -e
export MODEL="${MODEL:-Qwen/Qwen2.5-7B-Instruct}"
export MAX_LEN="${MAX_LEN:-16384}"
export PORT="${PORT:-8000}"
export VLLM_PORT="$PORT"
echo "[onstart] модель: $MODEL, порт: $PORT"
nvidia-smi || true
python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name "$(basename "$MODEL")" \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization 0.92 \
  --host 0.0.0.0 --port "$PORT" \
  --disable-log-requests &
echo "[onstart] vLLM поднят, health: http://<публичный-адрес>:$PORT/v1/models"
wait
