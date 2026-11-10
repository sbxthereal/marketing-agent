#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  else
    echo 'Install uv from https://docs.astral.sh/uv/getting-started/installation/ then rerun this script.'
    exit 1
  fi
fi
if [ ! -x .venv/bin/python ]; then uv venv --python 3.14.7 .venv; fi
uv pip install --python .venv/bin/python -r requirements.lock
if [ ! -f config.json ]; then cp config.browser.example.json config.json; fi
if [ ! -f .env ]; then cp .env.example .env; fi
chmod 600 config.json .env
.venv/bin/python -m pilot doctor
echo 'Installed. Follow START-HERE.md to configure the account and start the pilot.'
