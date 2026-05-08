#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
source "$HOME/.zshrc" >/dev/null 2>&1 || true
if [ -f ".venv/bin/activate" ]; then
  source ".venv/bin/activate"
fi
python3 app.py --host 127.0.0.1 --port 8765
