#!/usr/bin/env bash
# Stand up a pinned Superset sandbox (with example data) on any Docker host.
# Usage: sandbox/up.sh [--tag <version>] [port]
#   Default tag 6.1.0 keeps the original container/volume names (existing
#   sandboxes keep working); other tags get suffixed names and their own
#   default ports so versions run side by side on one host.
set -euo pipefail

TAG="6.1.0"
PORT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    *) PORT="$1"; shift ;;
  esac
done
if [ -z "$PORT" ]; then
  case "$TAG" in
    6.1.0) PORT=8098 ;;
    5.0.0) PORT=8095 ;;
    4.1.4) PORT=8094 ;;
    *) echo "no default port for tag ${TAG}; pass one: sandbox/up.sh --tag ${TAG} <port>" >&2; exit 2 ;;
  esac
fi

IMAGE="apache/superset:${TAG}"
if [ "$TAG" = "6.1.0" ]; then
  NAME="chartwright-superset"
  VOLUME="chartwright_superset_home"
else
  SUFFIX="${TAG//./-}"
  NAME="chartwright-superset-${SUFFIX}"
  VOLUME="chartwright_superset_home_${SUFFIX}"
fi
STATE_DIR="${HOME}/.config/chartwright"
SECRET_FILE="${STATE_DIR}/sandbox-secret.env"

mkdir -p "$STATE_DIR"
if [ ! -f "$SECRET_FILE" ]; then
  echo "SUPERSET_SECRET_KEY=$(openssl rand -base64 42 | tr -d '\n')" > "$SECRET_FILE"
  chmod 600 "$SECRET_FILE"
fi

docker volume create "$VOLUME" >/dev/null
if ! docker ps -a --format '{{.Names}}' | grep -q "^${NAME}$"; then
  docker pull "$IMAGE"
  docker run -d --name "$NAME" -p "${PORT}:8088" --env-file "$SECRET_FILE" \
    -v "${VOLUME}:/app/superset_home" "$IMAGE"
elif ! docker ps --format '{{.Names}}' | grep -q "^${NAME}$"; then
  docker start "$NAME"
fi

echo "waiting for superset ${TAG} on :${PORT} ..."
for _ in $(seq 1 60); do
  curl -sf "http://localhost:${PORT}/health" >/dev/null 2>&1 && break
  sleep 5
done
curl -sf "http://localhost:${PORT}/health" >/dev/null || { echo "superset did not come up"; exit 1; }

docker exec "$NAME" superset db upgrade >/dev/null 2>&1
docker exec "$NAME" superset fab create-admin --username admin --firstname Admin \
  --lastname User --email admin@localhost --password admin >/dev/null 2>&1 || true
docker exec "$NAME" superset init >/dev/null 2>&1
echo "loading example datasets (a few minutes on first run) ..."
docker exec "$NAME" superset load_examples >/dev/null 2>&1 || \
  docker exec "$NAME" superset load-examples >/dev/null 2>&1

cat <<EOF

Sandbox ready: http://localhost:${PORT}  (admin / admin, Superset ${TAG})

Profile for the CLI (~/.config/chartwright/profiles.toml):

  [local]
  base_url = "http://localhost:${PORT}"
  username = "admin"
  password_env = "CHARTWRIGHT_LOCAL_PASSWORD"

Then: export CHARTWRIGHT_LOCAL_PASSWORD=admin
EOF
