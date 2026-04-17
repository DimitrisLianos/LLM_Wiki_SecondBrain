#!/bin/bash
# watch raw/ for new files and auto-ingest them.
# backend is chosen automatically:
#   macos:        fswatch   (brew install fswatch)
#   linux / wsl:  inotifywait (apt install inotify-tools)
# run alongside the llama.cpp server in a third terminal tab.
# on native windows, run this script inside wsl.
#
# usage:
#   bash scripts/watch.sh           # watch and auto-ingest.
#   bash scripts/watch.sh --lint    # also run lint after each ingest.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
RAW_DIR="$PROJECT_DIR/obsidian_vault/raw"
RUN_LINT="${1:-}"

# pick an available filesystem-watch backend. fswatch is the macos default;
# inotifywait is the linux equivalent. we emit null-delimited paths either
# way so the read loop below stays identical.
if command -v fswatch &> /dev/null; then
    WATCH_CMD=(fswatch -0 --event Created "$RAW_DIR")
elif command -v inotifywait &> /dev/null; then
    WATCH_CMD=(inotifywait -m -q -e create --format '%w%f' "$RAW_DIR")
else
    echo "error: no filesystem watcher found. install one of:"
    echo "  macos:       brew install fswatch"
    echo "  debian/ubuntu: sudo apt install inotify-tools"
    exit 1
fi

echo "watching $RAW_DIR for new files..."
echo "press Ctrl+C to stop."
echo ""

# fswatch emits null-delimited paths (-0). inotifywait emits newline-delimited.
# route each through the same loop: we accept either record separator by
# feeding both tools' stdout through a tr that normalises to \n, then reading
# line by line.
"${WATCH_CMD[@]}" | tr '\0' '\n' | while IFS= read -r file; do
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
