#!/usr/bin/env bash
# Miss Sona — quick launcher
set -e
# Load .env if present
if [ -f .env ]; then
  set -a; source .env; set +a
fi
# Prefer venv if exists
if [ -d ".venv" ]; then
  source .venv/bin/activate
fi
echo "→ Starting Miss Sona..."
if [ "$BOT_TOKEN" = "PUT_YOUR_TOKEN_HERE" ] || [ -z "$BOT_TOKEN" ]; then
  echo "⚠️  BOT_TOKEN not set! Edit .env first."
  exit 1
fi
exec python miss_sona.py
