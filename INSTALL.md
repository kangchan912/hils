# 설치 방법

## 환경
- VMware#1 (192.168.239.185): MATLAB R202x, EnergyPlus 25.2.0, Python 3.x
- VMware#2 (192.168.239.189/190, hostname: pin): Docker, CORE 에뮬레이터

## 1. EnergyPlus 25.2.0
```bash
# VMware#1에서
wget https://github.com/NREL/EnergyPlus/releases/download/v25.2.0/EnergyPlus-25.2.0-...-Linux-Ubuntu22.04-x86_64.sh
chmod +x EnergyPlus-25.2.0-*.sh
sudo ./EnergyPlus-25.2.0-*.sh
export EP_DIR=/usr/local/EnergyPlus-25-2-0
```

## 2. Python 의존성 (VMware#1)
```bash
pip install pyenergyplus
```

## 3. MATLAB (VMware#1)
- 30일 평가판: https://www.mathworks.com/products/matlab/trial.html
- Instrument Control Toolbox 불필요 (Java DatagramSocket 사용)

## 4. Docker (VMware#2)
```bash
sudo apt install docker.io docker-compose
sudo usermod -aG docker $USER
```

## 5. CORE 에뮬레이터 (VMware#2)
```bash
pip install core-network
# 또는 패키지 설치
sudo apt install core-network
```

## 6. VILLASnode 커스텀 이미지 빌드 (VMware#2)
```bash
# 기본 이미지 pull
docker pull registry.git.rwth-aachen.de/acs/public/villas/node:latest

# gdb 포함 이미지 생성
docker run -d --name villas-tmp \
  registry.git.rwth-aachen.de/acs/public/villas/node:latest \
  tail -f /dev/null
docker exec villas-tmp apt-get update
docker exec villas-tmp apt-get install -y gdb

# VILLASnode 버그 패치 적용 (patches/ 참고)
docker cp patches/channel_sample_fix.patch villas-tmp:/tmp/
docker exec villas-tmp bash -c "
  cd /villas/build
  patch -p1 < /tmp/channel_sample_fix.patch
  make -j\$(nproc) && make install
"

# 이미지 저장
docker commit villas-tmp villas-node-core:local
docker rm -f villas-tmp
```

## 7. villas-signaling 이미지
```bash
docker pull registry.git.rwth-aachen.de/acs/public/villas/signaling:latest
docker tag villas-signaling:latest villas-signaling-core:local
```