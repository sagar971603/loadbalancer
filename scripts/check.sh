#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for script in "$ROOT"/scripts/*.sh; do bash -n "$script"; done

if find "$ROOT" -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.log' -o -name '*.pdf' \) -print -quit | grep -q .; then
  echo "Forbidden secret/data file found." >&2
  exit 1
fi

grep -qE '^[[:space:]]*ip_hash;' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -q '127.0.0.1:8009' "$ROOT/backup/backend/nginx/automation-v2"
echo "Checks passed."
