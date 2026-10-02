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

# avoid collision if automation and manual runs are going at the same time. 
# concurrent runs race the shared manifest and git wiki. mkdir is the atomic
# primitive (no flock on macOS); a stale lock from a crashed run is reclaimed once its pid is gone.
LOCK_DIR="$HOME/second-brain/.pipeline.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    holder="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
    if [ -n "$holder" ] && kill -0 "$holder" 2>/dev/null; then
        echo "[$LOG_PREFIX] A pipeline run is already in progress (pid $holder) — skipping this one."
        exit 0
    fi
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR" 2>/dev/null || {
        echo "[$LOG_PREFIX] Could not acquire the pipeline lock — skipping this one."
        exit 0
    }
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

echo "[$LOG_PREFIX] Starting second-brain pipeline (stage: $STAGE)"

exit_code=0

# A failing stage must not abort the script: ingest still continues into
# compile, and the process exits with the worst code that actually ran
# (1 outranks 2).
run_stage() {
    local label="$1"
    shift
    local stage_code=0
    "$@" 2>&1 || stage_code=$?
    if [ "$stage_code" -ne 0 ]; then
        echo "[$LOG_PREFIX] $label failed (exit $stage_code)"
    fi
    # Any code other than 2 (a launcher failure such as 127 included) counts
    # as a failure, so it is never reported as a partial run.
    if [ "$stage_code" -eq 2 ]; then
        if [ "$exit_code" -eq 0 ]; then
            exit_code=2
        fi
    elif [ "$stage_code" -ne 0 ]; then
        exit_code=1
    fi
}

if [ "$STAGE" = "drops" ]; then
    run_stage "Ingestion" uv run second-brain ingest --drops-only
fi
if [ "$STAGE" = "ingest" ] || [ "$STAGE" = "all" ]; then
    run_stage "Ingestion" uv run second-brain ingest
fi
if [ "$STAGE" = "compile" ] || [ "$STAGE" = "all" ]; then
    run_stage "Compilation" uv run second-brain compile
fi

echo "[$LOG_PREFIX] Pipeline complete"
exit "$exit_code"
