#!/bin/bash
set -e
IMAGE="registry.git.rwth-aachen.de/acs/public/villas/node:latest"
CONFIG_DIR="$(cd "$(dirname "$0")/config" && pwd)"
docker rm -f villas-node 2>/dev/null || true
docker run --name villas-node --network host --privileged \
    --volume "$CONFIG_DIR":/configs:ro --restart unless-stopped -d \
    "$IMAGE" node /configs/hils.conf
echo "[run] started. logs: docker logs -f villas-node"
