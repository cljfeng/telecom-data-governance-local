#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

.venv/bin/python -m pytest -q
node --check src/governance_app/static/app.js
node --check src/governance_app/static/ledger-data.js
node --check src/governance_app/static/rules.js
node --check src/governance_app/static/settings.js
node --check src/governance_app/static/analytics.js
bash -n scripts/start.sh
