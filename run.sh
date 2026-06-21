#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

export PATH="$HOME/.local/bin:$PATH"

# Mirror all output to a log so detached runs (launched by the menu bar
# app, which discards their stdout) leave a trace for diagnosing failures.
LOG_DIR="$HOME/second-brain/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/pipeline.log"
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt 2000000 ]; then
    mv -f "$LOG_FILE" "$LOG_FILE.1"
fi
exec > >(tee -a "$LOG_FILE") 2>&1

# Load .env (ANTHROPIC_API_KEY, etc.) so the compile + fallback stages
# can authenticate. Nothing in the Python code loads it automatically.
if [ -f .env ]; then
    set -a
    . ./.env
    set +a
fi

# Stage selector:
#   drops   — ingest only the drop folders (free, local). The app uses this
#             after an interactive drop so watched folders aren't swept too.
#   ingest  — full ingest of every source, drop folders and watched folders.
#   compile — build the wiki (paid, Claude).
#   all     — full ingest then compile (default; used by scheduled runs).
# The app only runs "compile" on an explicit action, so dropping never spends.
STAGE="${1:-all}"
LOG_PREFIX="$(date '+%Y-%m-%d %H:%M:%S')"

echo "[$LOG_PREFIX] Starting second-brain pipeline (stage: $STAGE)"

if [ "$STAGE" = "drops" ]; then
    uv run second-brain ingest --drops-only 2>&1 || echo "[$LOG_PREFIX] Ingestion failed"
fi
if [ "$STAGE" = "ingest" ] || [ "$STAGE" = "all" ]; then
    uv run second-brain ingest 2>&1 || echo "[$LOG_PREFIX] Ingestion failed"
fi
if [ "$STAGE" = "compile" ] || [ "$STAGE" = "all" ]; then
    uv run second-brain compile 2>&1 || echo "[$LOG_PREFIX] Compilation failed"
fi

echo "[$LOG_PREFIX] Pipeline complete"
