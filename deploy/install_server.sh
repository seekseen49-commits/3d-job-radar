#!/usr/bin/env bash
# Запускайте на Ubuntu вручную из корня распакованного проекта: sudo bash deploy/install_server.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Запустите через sudo: sudo bash deploy/install_server.sh" >&2
  exit 1
fi

APP_DIR=/opt/3d-job-radar
SERVICE_USER=3d-job-radar
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

apt-get update
apt-get install -y python3 python3-venv python3-pip rsync

if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR" "$APP_DIR/data" /etc/3d-job-radar
rsync -a --delete \
  --exclude '.venv/' --exclude '.env' --exclude '*.session' --exclude '*.session-journal' \
  --exclude '*.sqlite3' --exclude 'data/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  "$SOURCE_DIR/" "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

runuser -u "$SERVICE_USER" -- python3 -m venv "$APP_DIR/.venv"
runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -m pip install --upgrade pip
runuser -u "$SERVICE_USER" -- "$APP_DIR/.venv/bin/python" -m pip install -r "$APP_DIR/requirements.txt"

install -m 0644 "$APP_DIR/deploy/3d-job-radar.service" /etc/systemd/system/3d-job-radar.service
systemctl daemon-reload

echo "Установка файлов завершена. Секреты не созданы и не изменены."
echo "Создайте вручную /etc/3d-job-radar/job-radar.env из deploy/job-radar.env.example, затем:"
echo "  sudo chown root:3d-job-radar /etc/3d-job-radar/job-radar.env"
echo "  sudo chmod 640 /etc/3d-job-radar/job-radar.env"
echo "  sudo systemctl enable --now 3d-job-radar"
