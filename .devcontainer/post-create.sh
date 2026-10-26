#!/usr/bin/env bash
# One-time setup after the container is built.
set -euo pipefail

# The data volume is created root-owned; hand it to the vscode user
sudo chown -R "$(id -u):$(id -g)" "$HOME/.applypilot"

python -m pip install --upgrade pip
python -m pip install -e ".[dev,ui]"

# Job-board scraper (same pinned version the desktop app ships)
python -m pip install -r packaging/requirements-desktop.txt

# Chromium for the free local engine, plus the system libraries it needs
python -m playwright install --with-deps chromium

echo
echo "ApplyPilot is ready. The web UI starts automatically at http://localhost:8000"
