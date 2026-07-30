#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
for script in "$ROOT"/scripts/*.sh; do bash -n "$script"; done

if find "$ROOT" -type f \( -name '.env' -o -name '*.pem' -o -name '*.key' -o -name '*.log' -o -name '*.pdf' -o -name '*.htpasswd' \) -print -quit | grep -q .; then
  echo "Forbidden secret/data file found." >&2
  exit 1
fi

grep -qE '^[[:space:]]*ip_hash;' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+147[.]93[.]169[.]153:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+147[.]93[.]171[.]101:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+147[.]93[.]169[.]212:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+147[.]93[.]169[.]214:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+217[.]217[.]249[.]229:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE 'server[[:space:]]+217[.]216[.]58[.]27:80.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/automation_v2"
grep -qE '^[[:space:]]*ip_hash;' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -qE 'server[[:space:]]+147[.]93[.]169[.]212:8002.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -qE 'server[[:space:]]+147[.]93[.]169[.]214:8002.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -qE 'server[[:space:]]+217[.]217[.]249[.]229:8002.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -qE 'server[[:space:]]+217[.]216[.]58[.]27:8002.*weight=2' "$ROOT/backup/load-balancer/nginx/sites-available/regpan4.fskindia.com"
grep -q '127.0.0.1:8009' "$ROOT/backup/backend/nginx/automation-v2"
grep -q '127.0.0.1:8010' "$ROOT/backup/backend/nginx/registration"
[[ -f $ROOT/app/automation-v2/api/main.py ]]
[[ -f $ROOT/app/automation-v2/core/eportal_login_stealth_session.py ]]
[[ -f $ROOT/app/automation-v2/requirements.txt ]]
[[ -f $ROOT/app/eportal-hybrid/regmainhybrid.py ]]
[[ -f $ROOT/app/eportal-hybrid/registration_core.py ]]
[[ -f $ROOT/backup/backend/systemd/registration.service ]]
[[ -f $ROOT/dashboard/server.py ]]
[[ -f $ROOT/dashboard/lb-dashboard-control ]]
[[ -f $ROOT/scripts/deploy-dashboard.sh ]]
[[ -x $ROOT/scripts/activate-additional-egress.sh ]]
grep -q '217.217.249.229' "$ROOT/dashboard/lb-dashboard-control"
grep -q '217.216.58.27' "$ROOT/dashboard/lb-dashboard-control"
grep -q 'EGRESS_SLOTS_PER_IP=5' "$ROOT/backup/backend/systemd/egress-proxy-single.conf"

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
