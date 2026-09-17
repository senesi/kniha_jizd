#!/usr/bin/env bash
# Deploy Knihy jízd na VPS.
#
# Spouští se RUČNĚ na VPS, až když je změna otestovaná lokálně,
# commitnutá a pushnutá do origin/main. Žádný automatický deploy po
# pushi neexistuje.
#
# Použití (na VPS):
#   cd /opt/kniha_jizd/app && ./scripts/deploy_vps.sh
#
# Tenhle skript se nikdy nedotkne jiného projektu na sdíleném VPS
# (DSS, Evidence nářadí) - pracuje výhradně pod /opt/kniha_jizd a
# restartuje výhradně kontejner kniha-jizd-app.
#
# Pojistky:
#   - při jakékoliv chybě okamžitě končí (set -euo pipefail)
#   - odmítne deployovat přes necommitnuté změny na VPS
#   - před migracemi udělá zálohu databáze a skončí, když vyjde prázdná
#   - `alembic upgrade head` je idempotentní, takže je bezpečné ho pustit
#     i při deployi bez nové migrace
#   - nikdy nerestartuje kniha-jizd-postgres a nesahá na jeho volume
set -euo pipefail

APP_DIR="/opt/kniha_jizd/app"
DOCKER_DIR="/opt/kniha_jizd/docker"
BACKUP_DIR="/opt/kniha_jizd/backups/postgres"
ENV_FILE="/opt/kniha_jizd/config/.env"
HEALTHZ_URL="http://127.0.0.1:8002/healthz"
DB_NAME="kniha_jizd"
DB_USER="kniha_jizd_app"

echo "==> [1/7] Kontrola čistého working tree v ${APP_DIR}..."
cd "$APP_DIR"
if [ -n "$(git status --porcelain)" ]; then
  echo "CHYBA: ${APP_DIR} má necommitnuté změny - deploy by je přepsal." >&2
  git status --short
  exit 1
fi

echo "==> [2/7] Fetch a fast-forward na origin/main..."
git fetch origin main
BEFORE_SHA=$(git rev-parse HEAD)
git checkout main
git pull --ff-only origin main
AFTER_SHA=$(git rev-parse HEAD)
echo "    ${BEFORE_SHA} -> ${AFTER_SHA}"
if [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
  echo "    Beze změny - pokračuji (build/migrace/restart jsou idempotentní)."
fi

echo "==> [3/7] Záloha databáze ${DB_NAME}..."
mkdir -p "$BACKUP_DIR"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="${BACKUP_DIR}/kniha-jizd-pre-deploy-${TIMESTAMP}.sql"
sudo docker exec kniha-jizd-postgres pg_dump -U "$DB_USER" -d "$DB_NAME" -F c > "$BACKUP_FILE"
BACKUP_SIZE=$(stat -c%s "$BACKUP_FILE" 2>/dev/null || stat -f%z "$BACKUP_FILE")
if [ "$BACKUP_SIZE" -eq 0 ]; then
  echo "CHYBA: záloha ${BACKUP_FILE} je prázdná - končím dřív, než se čehokoliv dotknu." >&2
  exit 1
fi
echo "    záloha: ${BACKUP_FILE} (${BACKUP_SIZE} B)"

echo "==> [4/7] Build image kniha-jizd-app..."
cd "$DOCKER_DIR"
sudo docker compose build app

echo "==> [5/7] Migrace databáze..."
NET=$(sudo docker inspect kniha-jizd-postgres --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}')
sudo docker run --rm --network "$NET" --env-file "$ENV_FILE" kniha-jizd-app:latest alembic upgrade head

echo "==> [6/7] Restart pouze kontejneru kniha-jizd-app..."
sudo docker compose up -d --no-deps app

echo "==> [7/7] Čekám na /healthz..."
for i in $(seq 1 10); do
  if curl -sf "$HEALTHZ_URL" > /dev/null; then
    echo "    kniha-jizd-app je zdravá."
    echo ""
    echo "Deploy hotový: ${BEFORE_SHA} -> ${AFTER_SHA}"
    exit 0
  fi
  sleep 2
done

echo "CHYBA: kniha-jizd-app po deployi neodpověděla na ${HEALTHZ_URL}." >&2
echo "Zkontroluj: sudo docker logs kniha-jizd-app" >&2
exit 1
