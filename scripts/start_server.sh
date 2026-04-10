#!/bin/bash
# llm wiki — llama.cpp server (turboquant fork).
# tuned for macbook pro m5 2025 (32gb) + gemma 4 26b unsloth dynamic (ud).
#
# weights: ud (unsloth dynamic) per-layer importance-weighted quantization.
# kv cache: turboquant turbo4 via TheTom/llama-cpp-turboquant fork.
#   uses polarquant + walsh-hadamard rotation for 3.8x v-cache compression.
#   asymmetric config: q8_0 keys (full precision) + turbo4 values (compressed).
#
# WARNING: turbo3 is NOT safe for gemma 4 q4_k_m — use turbo4 only.
#   see: github.com/TheTom/turboquant_plus/docs/turboquant-recommendations.md
#
# memory budget (32gb):
#   model q4_k_m  ~16gb
#   macos+system  ~5gb
#   kv cache      ~11gb available
#   2 parallel slots × 32k ctx × q8_0 k + turbo4 v ≈ 3-4gb.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MODEL="$PROJECT_DIR/models/gemma-4-26B-A4B-it-UD-Q4_K_M.gguf"
LLAMA_SERVER="$PROJECT_DIR/llama.cpp/build/bin/llama-server"

HOST="127.0.0.1"
PORT=8080
CONTEXT=65536        # total context split across parallel slots.
PARALLEL=2           # concurrent request slots (2 × 32k each).
GPU_LAYERS=999
THREADS=$(sysctl -n hw.performancecores 2>/dev/null || echo 8)
BATCH=4096           # prompt processing batch size. higher = faster prefill.
KV_TYPE_K="q8_0"          # full precision keys (attention routing).
KV_TYPE_V="turbo4"        # turboquant 4-bit values (3.8x compression).

case "${1:-start}" in
    start)
        if ! [ -f "$LLAMA_SERVER" ]; then
            echo "Error: llama-server not found."
            echo "Build it:"
            echo "  cd $PROJECT_DIR/llama.cpp"
            echo "  cmake -B build -DGGML_METAL=ON -DCMAKE_BUILD_TYPE=Release"
            echo "  cmake --build build --config Release -j"
            exit 1
        fi

        if ! [ -f "$MODEL" ]; then
            echo "Error: Model not found at $MODEL"
            exit 1
        fi

        echo "┌──────────────────────────────────────────────┐"
        echo "│  LLM Wiki — Local LLM Server                  │"
        echo "├──────────────────────────────────────────────┤"
        echo "│  Model:    Gemma 4 26B-A4B Q4_K_M (UD)        │"
        echo "│  Context:  ${CONTEXT} tokens (${PARALLEL} slots)            │"
        echo "│  GPU:      Metal (all layers)                  │"
        echo "│  KV:       ${KV_TYPE_K} K / ${KV_TYPE_V} V (TurboQuant)      │"
        echo "│  Runtime:  llama-cpp-turboquant fork            │"
        echo "│  Threads:  ${THREADS} (performance cores)           │"
        echo "│  Batch:    ${BATCH}                               │"
        echo "│  URL:      http://${HOST}:${PORT}                │"
        echo "└──────────────────────────────────────────────┘"
        echo ""
        echo "Loading model (~30s for 16GB)... Ctrl+C to abort."
        echo ""

        "$LLAMA_SERVER" \
            --model "$MODEL" \
            --host "$HOST" \
            --port "$PORT" \
            --ctx-size "$CONTEXT" \
            --parallel "$PARALLEL" \
            --n-gpu-layers "$GPU_LAYERS" \
            --threads "$THREADS" \
            --batch-size "$BATCH" \
            --flash-attn on \
            --cache-type-k "$KV_TYPE_K" \
            --cache-type-v "$KV_TYPE_V" \
            --reasoning off
        ;;

    stop)
        echo "Stopping llama.cpp server..."
        pkill -f "llama-server.*gemma" 2>/dev/null && echo "Stopped." || echo "Not running."
        ;;

    status)
        if curl -s "http://$HOST:$PORT/health" > /dev/null 2>&1; then
            echo "Server is running at http://$HOST:$PORT"
            curl -s "http://$HOST:$PORT/health" | python3 -m json.tool 2>/dev/null
        else
            echo "Server is not running."
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
