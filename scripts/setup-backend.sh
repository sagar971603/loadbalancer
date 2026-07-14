#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }

echo "Automation V2 guided backend setup"
read -rsp "CLIENT_KEY: " client_key
echo
read -rsp "CAPTCHA_API_KEY: " captcha_api_key
echo
[[ -n $client_key && -n $captcha_api_key ]] || { echo "Both values are required." >&2; exit 1; }

umask 077
env_file=$(mktemp)
trap 'rm -f "$env_file"' EXIT
printf 'CLIENT_KEY=%s\nCAPTCHA_API_KEY=%s\n' "$client_key" "$captcha_api_key" > "$env_file"

ENV_FILE="$env_file" "$ROOT/scripts/deploy-backend.sh"
