#!/usr/bin/env bash
# One-shot installer for Second Brain (macOS).
#
# Installs Python dependencies, builds and installs the menu bar app into
# /Applications, creates the data directories, records the backend launcher path
# for the app, and prepares the OCR models. Inference models download on first launch.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$REPO_DIR"

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
uv run python - <<'PY'
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "second_brain.django_config.settings.runtime")
django.setup()

from second_brain.apps.preferences.services.user_settings import get_pipeline_config

get_pipeline_config().ensure_directories()
PY

echo "[3/5] Downloading Docling's layout, table, and formula (CodeFormulaV2) models..."
uv run python -c "from second_brain.apps.ingestion.services.parsing.docling_parser import DoclingParser; DoclingParser().load_models()" \
    || echo "  Skipped — models will download on the first typed PDF."

echo "[4/5] Preparing local OCR model (Chandra 2, 4-bit MLX)..."
# Converts Chandra 2 weights to a ~2.9 GB 4-bit MLX model. First run on a
# fresh machine downloads the source weights; otherwise it reuses the cache.
# Falls back to converting lazily on the first handwritten ingest.
uv run python -c "from second_brain.apps.ingestion.services.parsing.chandra_parser import ensure_mlx_model; ensure_mlx_model('4bit')" \
    || echo "  Skipped — will convert on the first handwritten ingest."

echo "[5/5] Building and installing the menu bar app..."
( cd gui/SecondBrainBar && ./bundle.sh >/dev/null )
rm -rf "/Applications/$APP"
cp -r "gui/SecondBrainBar/$APP" "/Applications/$APP"

echo
echo "Installed. \"$APP\" is in /Applications."
