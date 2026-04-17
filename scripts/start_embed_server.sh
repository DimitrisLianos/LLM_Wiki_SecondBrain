#!/bin/bash
# llm wiki — embedding server (bge-m3) on port 8081.
# second llama.cpp instance dedicated to dense embeddings for stage 5
# entity resolution (cross-lingual + historical drift handling).
#
# model: bge-m3 — baai, chen et al. 2024 (arxiv 2402.03216).
#   multilingual (100+ langs), 8192 ctx, 1024-dim output, ~2.2gb q4_k_m.
#   chosen for cross-lingual coverage (greek <-> english entity matching)
#   and long context support for paragraph-level comparisons.
#
# why a second server:
#   - the main gemma server can't serve both chat and embeddings on the
#     same port with different models loaded.
#   - embeddings are small, fast, and called 10-100x per ingest. a
#     dedicated instance avoids queueing behind long chat completions.
#   - running side-by-side costs ~2.5gb additional ram; the m5 has headroom.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

MODEL="$PROJECT_DIR/models/bge-m3-Q4_K_M.gguf"
LLAMA_SERVER="$PROJECT_DIR/llama.cpp/build/bin/llama-server"

HOST="127.0.0.1"
PORT=8081
CONTEXT=8192         # bge-m3 native max is 8192; matches paragraph-level use.
PARALLEL=1           # embeddings are sub-second; no need to split slots.
GPU_LAYERS=999
# thread count: honours pre-set THREADS, falls back to macos core count,
# then to a safe default. override via `THREADS=16 bash scripts/start_embed_server.sh`
# on linux/wsl where sysctl is unavailable.
THREADS="${THREADS:-$(sysctl -n hw.performancecores 2>/dev/null || echo 8)}"
BATCH=2048

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
            echo "Error: BGE-M3 model not found at $MODEL"
            echo ""
            echo "Download it:"
            echo "  mkdir -p $PROJECT_DIR/models"
            echo "  cd $PROJECT_DIR/models"
            echo "  curl -L -o bge-m3-Q4_K_M.gguf \\"
            echo "    https://huggingface.co/lm-kit/bge-m3-gguf/resolve/main/bge-m3-Q4_K_M.gguf"
            exit 1
        fi

        echo "┌──────────────────────────────────────────────┐"
        echo "│  LLM Wiki — BGE-M3 Embedding Server           │"
        echo "├──────────────────────────────────────────────┤"
        echo "│  Model:    BGE-M3 Q4_K_M (1024-dim)           │"
        echo "│  Context:  ${CONTEXT} tokens                          │"
        echo "│  GPU:      Metal (all layers)                  │"
        echo "│  Purpose:  stage 5 entity resolution           │"
        echo "│  URL:      http://${HOST}:${PORT}                │"
        echo "└──────────────────────────────────────────────┘"
        echo ""
        echo "Loading model (~5s for 2.2GB)... Ctrl+C to abort."
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
            --embedding \
            --pooling mean
        ;;

    stop)
        echo "Stopping bge-m3 embedding server..."
        pkill -f "llama-server.*bge-m3" 2>/dev/null && echo "Stopped." || echo "Not running."
        ;;

    status)
        if curl -s "http://$HOST:$PORT/health" > /dev/null 2>&1; then
            echo "Embedding server is running at http://$HOST:$PORT"
            curl -s "http://$HOST:$PORT/health" | python3 -m json.tool 2>/dev/null
        else
            echo "Embedding server is not running."
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|status}"
        exit 1
        ;;
esac
