#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# TTB Label Verifier — one-command local setup and run
#
# Usage:
#   ./run.sh
#
# What it does:
#   1. Creates a Python virtual environment if one doesn't exist
#   2. Installs/updates dependencies from deployment/requirements.txt
#   3. Checks for a .env with GEMINI_API_KEY, prompts if missing
#   4. Starts the FastAPI app with uvicorn
#
# Safe to re-run — it's idempotent. Ctrl+C stops the server.
# ─────────────────────────────────────────────────────────────────────────

set -e  # exit immediately if any command fails

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$REPO_ROOT/deployment"
VENV_DIR="$REPO_ROOT/venv"
PORT="${PORT:-8000}"

SETUP_ONLY=false
if [ "$1" = "--setup-only" ]; then
    SETUP_ONLY=true
fi

echo "──────────────────────────────────────────────"
echo " TTB Label Verifier — local setup"
echo "──────────────────────────────────────────────"

# ── 1. Find a Python 3.12+ interpreter ──────────────────────────────────────
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3 python; do
    if command -v "$candidate" &> /dev/null; then
        VERSION_STR=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
        MAJOR=$(echo "$VERSION_STR" | cut -d. -f1)
        MINOR=$(echo "$VERSION_STR" | cut -d. -f2)
        if [ -n "$MAJOR" ] && [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 12 ]; then
            PYTHON_BIN="$candidate"
            echo "✅ Found Python $VERSION_STR at: $(command -v "$candidate")"
            break
        fi
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    echo "❌ No Python 3.12+ interpreter found."
    echo "    This project requires Python 3.12 or newer."
    echo "    Install one from https://www.python.org/downloads/ and try again."
    exit 1
fi

# ── 2. Create virtual environment if missing ───────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
    echo "📦 Creating virtual environment at $VENV_DIR ..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    echo "✅ Virtual environment already exists."
fi

source "$VENV_DIR/bin/activate"

# ── 3. Install dependencies ────────────────────────────────────────────────
echo "📦 Installing dependencies from deployment/requirements.txt ..."
pip install --upgrade pip --quiet
pip install -r "$DEPLOY_DIR/requirements.txt" --quiet
echo "✅ Dependencies installed."

# ── 4. Check for .env / GEMINI_API_KEY ──────────────────────────────────────
ENV_FILE="$DEPLOY_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
    if [ -f "$DEPLOY_DIR/.env.sample" ]; then
        cp "$DEPLOY_DIR/.env.sample" "$ENV_FILE"
        echo "📝 Created $ENV_FILE from .env.sample — you need to add your key."
    else
        echo "GEMINI_API_KEY=" > "$ENV_FILE"
        echo "📝 Created a blank $ENV_FILE — you need to add your key."
    fi
fi

# Check whether a real key is actually set (not blank, not the placeholder)
if ! grep -q "^GEMINI_API_KEY=.\+" "$ENV_FILE" || grep -q "your-gemini-api-key-here" "$ENV_FILE"; then
    echo ""
    echo "⚠️  GEMINI_API_KEY is not set in $ENV_FILE"
    echo "    Get a free key at: https://aistudio.google.com/apikey"
    echo ""
    read -p "    Paste your Gemini API key now (or press Enter to skip): " USER_KEY
    if [ -n "$USER_KEY" ]; then
        # Replace the line in-place (works on both macOS and Linux sed)
        if [[ "$OSTYPE" == "darwin"* ]]; then
            sed -i '' "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$USER_KEY|" "$ENV_FILE"
        else
            sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$USER_KEY|" "$ENV_FILE"
        fi
        echo "✅ Saved to $ENV_FILE"
    else
        echo "⚠️  Skipping — the app will start but verification calls will fail"
        echo "    until you add a key to $ENV_FILE"
    fi
fi

# ── 5. Start the server (unless --setup-only) ───────────────────────────────
if [ "$SETUP_ONLY" = true ]; then
    echo ""
    echo "✅ Setup complete. Run './run.sh' (no flags) or 'make run' to start the server."
    exit 0
fi

echo ""
echo "──────────────────────────────────────────────"
echo " Starting TTB Label Verifier on port $PORT"
echo " Open: http://localhost:$PORT"
echo " Press Ctrl+C to stop"
echo "──────────────────────────────────────────────"
echo ""

cd "$DEPLOY_DIR"
exec uvicorn app.main:app --reload --port "$PORT"
