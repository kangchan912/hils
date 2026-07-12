# 1) 정리
docker exec signaling pkill -9 server 2>/dev/null
docker exec gw1 pkill -9 villas-node 2>/dev/null
docker exec gw2 pkill -9 villas-node 2>/dev/null
sleep 1

# 2) signaling
docker exec -d signaling /server
sleep 1

# 3) Router 정적 경로 (핵심! 자주 빠뜨렸던 부분)
sudo vcmd -c /tmp/pycore.1/Router1 -- ip route add 10.0.11.0/24 via 10.0.20.2 2>/dev/null
sudo vcmd -c /tmp/pycore.1/Router2 -- ip route add 10.0.10.0/24 via 10.0.20.1 2>/dev/null

# 4) gw1/gw2 기본 경로
docker exec gw1 ip route add default via 10.0.10.1 2>/dev/null
docker exec gw2 ip route add default via 10.0.11.1 2>/dev/null

# 5) LAN 우회 차단 (iptables + ip6tables, 지난번처럼 동적 조회)
GW1_PID=$(docker inspect -f '{{.State.Pid}}' gw1)
GW2_PID=$(docker inspect -f '{{.State.Pid}}' gw2)
sudo nsenter -t $GW1_PID -n iptables -A OUTPUT -d 192.168.239.251 -j DROP 2>/dev/null
sudo nsenter -t $GW1_PID -n iptables -A INPUT  -s 192.168.239.251 -j DROP 2>/dev/null
sudo nsenter -t $GW2_PID -n iptables -A OUTPUT -d 192.168.239.250 -j DROP 2>/dev/null
sudo nsenter -t $GW2_PID -n iptables -A INPUT  -s 192.168.239.250 -j DROP 2>/dev/null

# 6) villas-node 기동
docker exec -d gw1 sh -c "stdbuf -oL -eL villas node /config/hils.conf > /tmp/villas.log 2>&1"
docker exec -d gw2 sh -c "stdbuf -oL -eL villas node /config/hils.conf > /tmp/villas.log 2>&1"
sleep 8

# 7) 확인
docker exec gw1 grep -i "peer connection\|Failed sendto" /tmp/villas.log