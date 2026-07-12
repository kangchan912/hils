# COREEMUL/docker/villas-node-core/Dockerfile
#
# core_wan_topology.py 는 CORE의 DockerNode를 "tail -f /dev/null" placeholder로
# 띄운 뒤, 네트워크 배선이 끝나면 `docker exec -d <node> villas node ...` 로
# 실제 프로세스를 기동한다. 그런데 공식 villas/node 이미지는 ENTRYPOINT가
# "villas"로 고정돼 있어서 CORE가 넘기는 "tail -f /dev/null" 커맨드가
# ENTRYPOINT의 인자로 붙어버려 placeholder 자체가 실패한다.
# -> ENTRYPOINT를 비운 래퍼 이미지로 우회.
#
# 빌드:
#   docker build -t villas-node-core:local -f COREEMUL/docker/villas-node-core/Dockerfile COREEMUL/docker/villas-node-core

FROM registry.git.rwth-aachen.de/acs/public/villas/node:latest
ENTRYPOINT []
CMD []
