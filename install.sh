#!/usr/bin/env bash
# One-shot installer for Second Brain (macOS).
#
# Installs Python dependencies, builds and installs the menu bar app into
# /Applications, creates the data directories, records the pipeline path
# for the app, and pulls the local models if Ollama is available.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

VAULT="$HOME/second-brain"
APP="Second Brain.app"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "Second Brain supports macOS only." >&2
    exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "uv is required but not installed. Install it with:" >&2
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

if ! command -v swift >/dev/null 2>&1; then
    echo "The Swift toolchain is required to build the app." >&2
    echo "Install Xcode or the Command Line Tools: xcode-select --install" >&2
    exit 1
fi

echo "[1/5] Installing Python dependencies..."
uv sync

echo "[2/5] Creating data directories..."
uv run python -c "from second_brain.config import load_config; load_config().ensure_directories()"

echo "[3/5] Recording pipeline path for the app..."
mkdir -p "$VAULT"
printf '%s\n' "$REPO_DIR/run.sh" > "$VAULT/.pipeline-script"

echo "[4/6] Pulling local models..."
if command -v ollama >/dev/null 2>&1; then
    ollama pull gemma3:4b || true
    ollama pull nomic-embed-text || true
elif curl -s --max-time 3 http://localhost:11434/api/tags >/dev/null 2>&1; then
    echo "  Ollama is running but its CLI is not on PATH. Pull the models with:"
    echo "    ollama pull gemma3:4b && ollama pull nomic-embed-text"
else
    echo "  Ollama not detected; triage and semantic search will stay inactive."
    echo "  Install from https://ollama.com, then run:"
    echo "    ollama pull gemma3:4b && ollama pull nomic-embed-text"
fi

echo "[5/6] Preparing local OCR model (Chandra 4-bit MLX)..."
# Converts the Chandra weights to a ~2.9 GB 4-bit MLX model. First run on a
# fresh machine downloads the source weights; otherwise it reuses the cache.
# Falls back to converting lazily on the first handwritten ingest.
uv run python -c "from second_brain.parsing.chandra_parser import ensure_mlx_model; ensure_mlx_model('4bit')" \
    || echo "  Skipped — will convert on the first handwritten ingest."

echo "[6/6] Building and installing the menu bar app..."
( cd gui/SecondBrainBar && ./bundle.sh >/dev/null )
rm -rf "/Applications/$APP"
cp -r "gui/SecondBrainBar/$APP" "/Applications/$APP"

echo
echo "Installed. \"$APP\" is in /Applications."
