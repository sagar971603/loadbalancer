#!/usr/bin/env bash
set -Eeuo pipefail

SECONDARY_IP="${1:?Usage: sudo $0 <secondary-ip> <proxy-instance> --drained}"
INSTANCE="${2:?Usage: sudo $0 <secondary-ip> <proxy-instance> --drained}"
[[ ${3:-} == --drained ]] || { echo "Disable both load-balancer routes and drain sessions first; then add --drained." >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 2; }
[[ $SECONDARY_IP =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ && $INSTANCE =~ ^[0-9]+$ ]] || { echo "Invalid IP or proxy instance." >&2; exit 2; }

PROXY_SERVICE="tinyproxy-egress@$INSTANCE"
PROXY_CONFIG="/etc/tinyproxy/egress-$INSTANCE.conf"
AUTO_DROPIN=/etc/systemd/system/automation-v2.service.d/egress.conf
REG_DROPIN=/etc/systemd/system/registration.service.d/egress.conf
READY=/root/additional-ip-ready

[[ -f $PROXY_CONFIG ]] || { echo "Missing staged proxy config: $PROXY_CONFIG" >&2; exit 3; }
grep -qx "Bind $SECONDARY_IP" "$PROXY_CONFIG" || { echo "Staged proxy does not bind $SECONDARY_IP." >&2; exit 3; }
[[ -f $READY/automation-egress-dual.conf && -f $READY/registration-egress-dual.conf ]] || {
  echo "Staged dual-IP service files are missing." >&2
  exit 3
}

systemctl enable --now "$PROXY_SERVICE"
ACTUAL=$(curl -fsS --proxy http://127.0.0.1:18889 --max-time 20 https://1.1.1.1/cdn-cgi/trace | sed -n 's/^ip=//p')
if [[ $ACTUAL != "$SECONDARY_IP" ]]; then
  systemctl disable --now "$PROXY_SERVICE"
  echo "Secondary proxy returned '${ACTUAL:-no IP}', expected $SECONDARY_IP; nothing activated." >&2
  exit 4
fi

STAMP=$(date +%Y%m%d-%H%M%S)
cp -a "$AUTO_DROPIN" "$READY/automation-egress-single.$STAMP.conf"
cp -a "$REG_DROPIN" "$READY/registration-egress-single.$STAMP.conf"
install -m 0644 "$READY/automation-egress-dual.conf" "$AUTO_DROPIN"
install -m 0644 "$READY/registration-egress-dual.conf" "$REG_DROPIN"
systemctl daemon-reload

if ! systemctl restart automation-v2 registration \
  || ! curl -fsS --retry 10 --retry-delay 2 --retry-connrefused http://127.0.0.1/health >/dev/null \
  || ! curl -fsS --retry 10 --retry-delay 2 --retry-connrefused http://127.0.0.1:8002/health >/dev/null; then
  install -m 0644 "$READY/automation-egress-single.$STAMP.conf" "$AUTO_DROPIN"
  install -m 0644 "$READY/registration-egress-single.$STAMP.conf" "$REG_DROPIN"
  systemctl daemon-reload
  systemctl restart automation-v2 registration
  systemctl disable --now "$PROXY_SERVICE"
  echo "Application validation failed; single-IP configuration restored." >&2
  exit 5
fi

echo "Secondary egress $SECONDARY_IP activated. Set this backend's Newtool and Registration weights from 1 to 2, then re-enable both routes."
