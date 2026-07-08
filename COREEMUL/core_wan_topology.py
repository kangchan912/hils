#!/usr/bin/env python3
# =====================================================================
# COREEMUL/core_wan_topology.py
#
#   kangchan912/hils 레포 구조에 맞춘 CORE WAN 토폴로지 스크립트.
#   VMware#2(192.168.239.159) 위에서 실행하는 것을 전제로 함.
#
#   gw1(VILLASnode #1, config/VILLASnode.1), gw2(VILLASnode #2,
#   config/VILLASnode.2) 각각 인터페이스 3개:
#     eth0 : 실제 LAN(Rj45 물리 인터페이스 경유) -> VMware#1(192.168.239.185,
#            MATLAB/EnergyPlus#1/#2)
#     eth1 : gw1<->gw2 CORE WAN 링크 전용 (delay/jitter/loss 적용)
#     eth2 : 시그널링 서버 전용 제어망 (gw1<->gw2 직접 라우팅 없음)
#
#   실행 후 콘솔에 찍히는 IP를 각 hils.conf 의 webrtc_a.server / webrtc_c.server
#   에 채워 넣고, 세션을 종료했다가 다시 실행하면 됩니다 — 같은 순서로
#   session.add_node() 를 호출하므로 노드 ID(및 그에 따른 IP)는 재실행해도
#   동일하게 재현됩니다.
#
#   ※ 이 환경(샌드박스)엔 CORE가 설치돼 있지 않아 실행 검증은 못 했습니다.
#     Rj45Node/DockerNode/LinkOptions API는 coreemu/core 공식 소스에서
#     직접 확인했습니다.
# =====================================================================

import argparse
import logging
import os

from core.emulator.coreemu import CoreEmu
from core.emulator.data import IpPrefixes, LinkOptions
from core.emulator.enumerations import EventTypes
from core.nodes.docker import DockerNode
from core.nodes.network import SwitchNode
from core.nodes.physical import Rj45Node

logging.basicConfig(level=logging.INFO)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG1 = os.path.join(REPO_ROOT, "config", "VILLASnode.1")
DEFAULT_CONFIG2 = os.path.join(REPO_ROOT, "config", "VILLASnode.2")


def parse_args():
    p = argparse.ArgumentParser(description="HILS CORE WAN 토폴로지 (kangchan912/hils)")
    p.add_argument("--villas-image", default="registry.git.rwth-aachen.de/acs/public/villas/node:latest")
    p.add_argument("--signaling-image", default="villas-signaling:local",
                    help="미리 `docker build -t villas-signaling:local .` 필요")
    p.add_argument("--config1-dir", default=DEFAULT_CONFIG1,
                    help=f"VILLASnode #1 설정 폴더 (기본: {DEFAULT_CONFIG1})")
    p.add_argument("--config2-dir", default=DEFAULT_CONFIG2,
                    help=f"VILLASnode #2 설정 폴더 (기본: {DEFAULT_CONFIG2})")
    p.add_argument("--nic-a", required=True, help="gw1 쪽 실제 LAN NIC 이름 (예: eth0)")
    p.add_argument("--nic-c", required=True, help="gw2 쪽 실제 LAN NIC 이름 (물리 NIC 하나뿐이면 --nic-a 와 동일값)")
    p.add_argument("--delay-us", type=int, default=80000)
    p.add_argument("--jitter-us", type=int, default=20000)
    p.add_argument("--loss-pct", type=float, default=1.0)
    return p.parse_args()


def main():
    args = parse_args()

    for d in (args.config1_dir, args.config2_dir):
        if not os.path.isfile(os.path.join(d, "hils.conf")):
            raise SystemExit(f"hils.conf 를 찾을 수 없습니다: {d}/hils.conf")

    coreemu = CoreEmu()
    session = coreemu.create_session()
    session.set_state(EventTypes.CONFIGURATION_STATE)

    try:
        prefix_lan = IpPrefixes(ip4_prefix="192.168.239.0/24")   # VMware#1 이 있는 실제 대역
        prefix_wan = IpPrefixes(ip4_prefix="10.0.9.0/30")
        prefix_sig_a = IpPrefixes(ip4_prefix="10.0.10.0/30")
        prefix_sig_c = IpPrefixes(ip4_prefix="10.0.11.0/30")

        opts1 = DockerNode.create_options()
        opts1.image = args.villas_image
        opts1.volumes = [(args.config1_dir, "/config", False, False)]
        gw1 = session.add_node(DockerNode, options=opts1)

        opts2 = DockerNode.create_options()
        opts2.image = args.villas_image
        opts2.volumes = [(args.config2_dir, "/config", False, False)]
        gw2 = session.add_node(DockerNode, options=opts2)

        opts_sig = DockerNode.create_options()
        opts_sig.image = args.signaling_image
        signaling = session.add_node(DockerNode, options=opts_sig)

        # gw1.eth0 : Rj45(물리 NIC) -- switch -- gw1 -- (VMware#1: 185)
        rj45_a = session.add_node(Rj45Node, name=args.nic_a)
        switch_a = session.add_node(SwitchNode)
        session.add_link(rj45_a.id, switch_a.id)
        iface1_lan = prefix_lan.create_iface(gw1)
        session.add_link(gw1.id, switch_a.id, iface1_lan)

        # gw2.eth0 : Rj45(물리 NIC) -- switch -- gw2 -- (VMware#1: 185, EnergyPlus#2)
        rj45_c = session.add_node(Rj45Node, name=args.nic_c)
        switch_c = session.add_node(SwitchNode)
        session.add_link(rj45_c.id, switch_c.id)
        iface2_lan = prefix_lan.create_iface(gw2)
        session.add_link(gw2.id, switch_c.id, iface2_lan)

        # gw1.eth1 <-> gw2.eth1 : CORE WAN 링크
        wan_options = LinkOptions(delay=args.delay_us, jitter=args.jitter_us, loss=args.loss_pct)
        iface1_wan = prefix_wan.create_iface(gw1)
        iface2_wan = prefix_wan.create_iface(gw2)
        session.add_link(gw1.id, gw2.id, iface1_wan, iface2_wan, options=wan_options)

        # gw1.eth2 <-> signaling (제어망 A)
        iface1_sig = prefix_sig_a.create_iface(gw1)
        sig_iface_a = prefix_sig_a.create_iface(signaling)
        session.add_link(gw1.id, signaling.id, iface1_sig, sig_iface_a)

        # gw2.eth2 <-> signaling (제어망 C)
        iface2_sig = prefix_sig_c.create_iface(gw2)
        sig_iface_c = prefix_sig_c.create_iface(signaling)
        session.add_link(gw2.id, signaling.id, iface2_sig, sig_iface_c)

        session.instantiate()

        print("=" * 70)
        print("CORE WAN 세션 기동 완료")
        print(f"  gw1(VILLASnode#1) eth0(LAN)={iface1_lan.ip4}  eth2(SIG)={iface1_sig.ip4}")
        print(f"  gw2(VILLASnode#2) eth0(LAN)={iface2_lan.ip4}  eth2(SIG)={iface2_sig.ip4}")
        print(f"  signaling  sigA={sig_iface_a.ip4}  sigC={sig_iface_c.ip4}")
        print(f"  WAN 링크: delay={args.delay_us}us jitter={args.jitter_us}us loss={args.loss_pct}%")
        print("=" * 70)
        print("다음을 각 hils.conf 에 채워 넣고, 이 스크립트를 재시작하세요:")
        print(f"  config/VILLASnode.1/hils.conf  webrtc_a.server = \"ws://{sig_iface_a.ip4}:8080\"")
        print(f"  config/VILLASnode.2/hils.conf  webrtc_c.server = \"ws://{sig_iface_c.ip4}:8080\"")
        print("  (재시작해도 노드 생성 순서가 같아 IP는 동일하게 재현됩니다)")
        print("=" * 70)

        input("종료하려면 Enter를 누르세요...")
    finally:
        coreemu.shutdown()


if __name__ == "__main__":
    main()