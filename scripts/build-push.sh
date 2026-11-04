#!/usr/bin/env bash
# Build the usage-web image for the lab's architectures and push it to the lab registry.
# Run on the primary node from the repo root.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

REGISTRY="${REGISTRY:-registry.lab:5000}"
IMAGE="${REGISTRY}/lab/usage-web"
TAG="${TAG:-$(git rev-parse --short HEAD)}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

if [[ "${ALLOW_DIRTY:-0}" != "1" && -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty; commit or stash, or set ALLOW_DIRTY=1" >&2
  exit 1
fi

docker buildx build --builder default --platform "$PLATFORMS" \
  -f web/Dockerfile -t "$IMAGE:$TAG" -t "$IMAGE:latest" --push .

echo "TAG=$TAG"
