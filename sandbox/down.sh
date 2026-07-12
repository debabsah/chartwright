#!/usr/bin/env bash
# Stop a sandbox. Usage: sandbox/down.sh [--tag <version>] [--purge]
# --purge also deletes the container and its data volume.
set -euo pipefail

TAG="6.1.0"
PURGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ "$TAG" = "6.1.0" ]; then
  NAME="chartwright-superset"
  VOLUME="chartwright_superset_home"
else
  SUFFIX="${TAG//./-}"
  NAME="chartwright-superset-${SUFFIX}"
  VOLUME="chartwright_superset_home_${SUFFIX}"
fi

docker stop "$NAME" 2>/dev/null || true
if [ -n "$PURGE" ]; then
  docker rm "$NAME" 2>/dev/null || true
  docker volume rm "$VOLUME" 2>/dev/null || true
  echo "sandbox ${TAG} purged"
else
  echo "sandbox ${TAG} stopped (data kept; sandbox/up.sh --tag ${TAG} restarts it)"
fi
