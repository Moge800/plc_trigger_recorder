#!/usr/bin/env bash
# PLC Trigger Recorder — Linux / Raspberry Pi launcher
set -euo pipefail
cd "$(dirname "$0")"
uv sync
uv run src/main.py
