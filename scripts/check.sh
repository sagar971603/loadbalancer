#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for script in "$ROOT"/scripts/*.sh; do bash -n "$script"; done

if find "$ROOT" -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.log' -o -name '*.pdf' \) -print -quit | grep -q .; then
  echo "Forbidden secret/data file found." >&2
  exit 1
fi

grep -qE '^[[:space:]]*ip_hash;' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE '^[[:space:]]*ip_hash;' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -q '127.0.0.1:8009' "$ROOT/backup/backend/nginx/automation-v2"
grep -q '127.0.0.1:8010' "$ROOT/backup/backend/nginx/registration"
[[ -f $ROOT/app/automation-v2/api/main.py ]]
[[ -f $ROOT/app/automation-v2/core/eportal_login_stealth_session.py ]]
[[ -f $ROOT/app/automation-v2/requirements.txt ]]
[[ -f $ROOT/app/eportal-hybrid/regmainhybrid.py ]]
[[ -f $ROOT/app/eportal-hybrid/registration_core.py ]]
[[ -f $ROOT/backup/backend/systemd/registration.service ]]

if grep -RInE --include='*.py' --include='*.md' 'print\(pan, password\)' "$ROOT/app"; then
  echo "Credential-printing code found." >&2
  exit 1
fi

if grep -RInE --include='*.py' --include='*.md' '[A-Z]{5}[0-9]{4}[A-Z]' "$ROOT/app" | grep -vE 'ABCDE1234F|AAAAA9999A'; then
  echo "Unsanitized PAN example found." >&2
  exit 1
fi

if grep -RInE --include='*.py' --include='*.md' \
  '["'"'](PASSWORD|CONFIRMPWD)["'"'][[:space:]]*:[[:space:]]*["'"']' "$ROOT/app" | grep -v 'your-password-here'; then
  echo "Unsanitized password example found." >&2
  exit 1
fi
echo "Checks passed."
