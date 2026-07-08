#!/bin/bash
set -e
# =====================================================================
# run.sh — VILLASnode #1 또는 #2 를 CORE 없이 단독으로 빠르게 띄우는 스크립트
#
# [수정] 예전 버전은 "config/hils.conf" 하나만 있던 시절 기준이라
#   지금 구조(config/VILLASnode.1/, config/VILLASnode.2/)와 경로가 안 맞았음.
#   -> 어느 노드를 띄울지 인자로 받도록 변경.
# [수정] hils.conf 안의 hook 경로가 "/config/hooks/..."(단수)인데 예전엔
#   "/configs"(복수)로 마운트해서 hook을 못 찾는 문제가 있었음 -> "/config"로 통일.
#
# 사용법:
#   ./run.sh 1   # VILLASnode #1 (config/VILLASnode.1/hils.conf) 기동
#   ./run.sh 2   # VILLASnode #2 (config/VILLASnode.2/hils.conf) 기동
#
# 참고: 이건 CORE 없이 단독 확인용입니다. 최종 WAN 구성(webrtc_a <-> webrtc_c)은
# COREEMUL/ 쪽 스크립트로 두 인스턴스를 함께 관리하는 것이 정식 경로입니다.
# =====================================================================

NODE_NUM="${1:?사용법: ./run.sh 1  또는  ./run.sh 2}"

case "$NODE_NUM" in
    1|2) ;;
    *) echo "1 또는 2만 지정하세요 (예: ./run.sh 1)"; exit 1 ;;
esac

IMAGE="registry.git.rwth-aachen.de/acs/public/villas/node:latest"
REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
CONFIG_DIR="${REPO_ROOT}/config/VILLASnode.${NODE_NUM}"
CONTAINER_NAME="villas-node-${NODE_NUM}"

if [ ! -f "${CONFIG_DIR}/hils.conf" ]; then
    echo "hils.conf 를 찾을 수 없습니다: ${CONFIG_DIR}/hils.conf"
    exit 1
fi

docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true
docker run --name "${CONTAINER_NAME}" --network host --privileged \
    --volume "${CONFIG_DIR}":/config:ro --restart unless-stopped -d \
    "${IMAGE}" node /config/hils.conf

echo "[run] ${CONTAINER_NAME} started. logs: docker logs -f ${CONTAINER_NAME}"
