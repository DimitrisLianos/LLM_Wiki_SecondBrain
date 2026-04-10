#!/bin/bash
# watch raw/ for new files and auto-ingest them.
# uses fswatch (brew install fswatch) to monitor the directory.
# run alongside the llama.cpp server in a third terminal tab.
#
# usage:
#   bash scripts/watch.sh           # watch and auto-ingest.
#   bash scripts/watch.sh --lint    # also run lint after each ingest.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RAW_DIR="$PROJECT_DIR/obsidian_vault/raw"
RUN_LINT="${1:-}"

if ! command -v fswatch &> /dev/null; then
    echo "error: fswatch not found. install it:"
    echo "  brew install fswatch"
    exit 1
fi

echo "watching $RAW_DIR for new files..."
echo "press Ctrl+C to stop."
echo ""

fswatch -0 --event Created "$RAW_DIR" | while IFS= read -r -d '' file; do
    # skip hidden files and directories.
    basename="$(basename "$file")"
    if [[ "$basename" == .* ]] || [[ -d "$file" ]]; then
        continue
    fi

    # small delay to let the file finish writing.
    sleep 2

    echo "[$(date '+%H:%M:%S')] new file detected: $basename"
    python3 "$SCRIPT_DIR/ingest.py" "$basename" || echo "  ingest failed for $basename"

    if [[ "$RUN_LINT" == "--lint" ]]; then
        python3 "$SCRIPT_DIR/lint.py"
    fi

    echo ""
done
