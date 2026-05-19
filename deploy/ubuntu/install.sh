#!/usr/bin/env bash
# Install Forecast app on Ubuntu (API + scan timer).
# Run as root: sudo bash deploy/ubuntu/install.sh
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/forecast}"
APP_USER="${APP_USER:-forecast}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root: sudo bash $0"
  exit 1
fi

if [[ ! -d "$APP_DIR" ]]; then
  echo "APP_DIR $APP_DIR does not exist. Clone the repo there first, e.g.:"
  echo "  git clone <your-repo> $APP_DIR"
  exit 1
fi

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip

if ! id "$APP_USER" &>/dev/null; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

chown -R "$APP_USER:$APP_USER" "$APP_DIR"

sudo -u "$APP_USER" bash -c "
  cd '$APP_DIR'
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r requirements.txt
"

ENV_FILE="$APP_DIR/deploy/ubuntu/forecast.env"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$APP_DIR/deploy/ubuntu/forecast.env.example" "$ENV_FILE"
  chown "$APP_USER:$APP_USER" "$ENV_FILE"
fi

install -m 644 "$APP_DIR/deploy/ubuntu/forecast-api.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/ubuntu/forecast-scan.service" /etc/systemd/system/
install -m 644 "$APP_DIR/deploy/ubuntu/forecast-scan.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable forecast-api.service forecast-scan.timer
systemctl restart forecast-api.service
systemctl start forecast-scan.service || true
systemctl start forecast-scan.timer

echo ""
echo "Done."
echo "  Panel:  http://$(hostname -I | awk '{print $1}'):8000/scanner"
echo "  Logs:   journalctl -u forecast-api -f"
echo "  Scan:   journalctl -u forecast-scan -f"
echo "  Timer:  systemctl list-timers forecast-scan.timer"
