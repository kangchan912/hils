"""
ep_socket_sim.py
────────────────────────────────────────────────────────────────────────
Lab C (EnergyPlus) — Python API 기반 실시간 소켓 시뮬레이터

담당 단계:
  [5] VILLASnode로부터 소켓(UDP)으로 값을 수신하고, 파이썬 기반으로
      EnergyPlus 연산(계산)을 수행
  [6] 계산한 결과값을 소켓(UDP)을 통해 다시 VILLASnode로 전송

VILLASnode 연동 흐름:
  [수신, STEP4->5] VILLASnode ─12001→ 이 코드
         villas.human 포맷 (4ch):
           [0] outdoor_temp     (°C)
           [1] solar_rad        (W/m²)
           [2] rel_humidity     (%)
           [3] cooling_setpoint (°C)  ← Lab A HVAC Controller 의 제어입력

  [송신, STEP6->7] 이 코드 ─12003→ VILLASnode
         villas.human 포맷 (2ch):
           [0] indoor_temp (°C)
           [1] hvac_power  (W)        ← EP 가 계산한 냉방전력(Cooling Rate)

EnergyPlus 연동 (폐루프):
  - 외기온도      → EMS Actuator "Outdoor Air Drybulb Temperature"
  - 일사량        → EMS Actuator "Diffuse Solar"
  - cooling_setpoint → EMS Actuator "Cooling SP Sch" (Schedule Value)
                       Lab A 가 보낸 설정온도가 실제 EP 냉방 제어에 반영됨
  - 출력          → Zone Mean Air Temperature, Zone Ideal Loads Cooling Rate

동작 방식 (이벤트 구동):
  - Lab A 에서 새 값이 도착했을 때만 EnergyPlus 가 한 스텝 전진하고 송신/로그.
  - 새 값이 없으면 EnergyPlus 콜백은 조용히 대기.
  - Ctrl+C 시 즉시 종료.

실행:
  python3 ep_socket_sim.py model.idf weather.epw
  python3 ep_socket_sim.py model.idf weather.epw \\
      --local-port 12001 --remote-host 192.168.239.159 --remote-port 12003
────────────────────────────────────────────────────────────────────────
"""

import argparse
import contextlib
import logging
import os
import socket
import sys
import threading
import time
from typing import List, Optional, Tuple

# ── EnergyPlus API 경로 설정 ─────────────────────────────────────────
EP_DIR = os.environ.get("EP_DIR", "/usr/local/EnergyPlus-25-2-0")
sys.path.insert(0, EP_DIR)
os.environ["LD_LIBRARY_PATH"] = EP_DIR + ":" + os.environ.get("LD_LIBRARY_PATH", "")

try:
    from pyenergyplus.api import EnergyPlusAPI
    print(f"[OK] pyenergyplus 로드: {EP_DIR}")
except ImportError as e:
    print(f"[FAIL] pyenergyplus 로드 실패: {e}")
    print("  EP_DIR 환경변수 또는 --ep-dir 인자를 확인하세요.")
    sys.exit(1)

logging.basicConfig(
    level   = logging.INFO,
    format  = "[%(asctime)s] %(levelname)-7s — %(message)s",
    datefmt = "%H:%M:%S",
    stream  = sys.stdout,
)
logger = logging.getLogger("ep_sim")


@contextlib.contextmanager
def _ep_quiet():
    """
    EnergyPlus C 레벨 stdout/stderr(fd 1,2)를 /dev/null 로 차단.
    Python 로거는 미리 복사한 fd 를 통해 계속 출력.
    set_console_output_status(False) 가 막지 못하는
    'Program terminated' 같은 C 레벨 메시지를 완전히 억제.
    """
    sys.stdout.flush()
    sys.stderr.flush()

    swapped: list = []
    for h in logging.root.handlers:
        if isinstance(h, logging.StreamHandler):
            try:
                fd_copy = os.dup(h.stream.fileno())
                new_stream = os.fdopen(fd_copy, "w", buffering=1)
                swapped.append((h, h.stream, new_stream))
                h.stream = new_stream
            except Exception:
                pass

    dn = os.open(os.devnull, os.O_WRONLY)
    old1, old2 = os.dup(1), os.dup(2)
    os.dup2(dn, 1)
    os.dup2(dn, 2)
    os.close(dn)
    try:
        yield
    finally:
        os.dup2(old1, 1); os.close(old1)
        os.dup2(old2, 2); os.close(old2)
        for h, orig, tmp in swapped:
            tmp.flush()
            h.stream = orig
            try:
                tmp.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────
# villas.human 유틸  ([STEP5] 수신 파싱 / [STEP6] 송신 인코딩)
# ─────────────────────────────────────────────────────────────────────
def parse_villas(raw: bytes) -> Optional[Tuple[int, List[float]]]:
    """
    [STEP5] villas.human 바이트 → (seq, [values]).

    처리 가능한 형식:
      <sec>.<nsec>(<seq>)\t<v1>\t<v2>\t...          (표준)
      <sec>.<nsec>(<seq>)\tname=v1\tname=v2\t...    (signal 이름 포함)
      <sec>.<nsec>(<seq>) <v1> <v2> ...              (공백 구분)
    여러 줄이면 마지막 완전한 줄을 사용.
    실패 시 None.
    """
    try:
        text = raw.decode("utf-8").strip()
        if not text or text.startswith("#"):
            return None

        lines = [l.strip() for l in text.splitlines()
                 if l.strip() and not l.strip().startswith("#")]
        if not lines:
            return None
        line = lines[-1]

        lp = line.index("(")
        rp = line.index(")", lp)
        seq = int(line[lp + 1 : rp])

        data_part = line[rp + 1:].strip()

        tokens = [t.strip() for t in data_part.replace("\t", " ").split() if t.strip()]

        values: List[float] = []
        for tok in tokens:
            if "=" in tok:
                tok = tok.split("=", 1)[1]
            num = ""
            for ch in tok:
                if ch.isdigit() or ch in ".-+eE":
                    num += ch
                elif num:
                    break
            if num:
                try:
                    values.append(float(num))
                except ValueError:
                    pass

        return (seq, values) if values else None

    except Exception as e:
        logger.debug("[parse] 실패: raw=%r err=%s", raw[:120], e)
        return None


def make_villas(values: List[float], seq: int) -> bytes:
    """[STEP6] (values, seq) → villas.human 바이트."""
    now  = time.time()
    sec  = int(now)
    nsec = int((now - sec) * 1e9)
    vals = "\t".join(f"{v:.6f}" for v in values)
    return f"{sec}.{nsec:09d}({seq})\t{vals}\n".encode("utf-8")


# ─────────────────────────────────────────────────────────────────────
# EnergyPlus 소켓 시뮬레이터
# ─────────────────────────────────────────────────────────────────────
class EPSocketSim:
    """
    [STEP5] VILLASnode로부터 기상값+제어입력을 UDP로 받아 EnergyPlus에
    주입하고 계산을 수행한다.
    [STEP6] 계산 결과(실내온도, HVAC 전력)를 다시 VILLASnode로 UDP 송신한다.

    이벤트 구동:
      - Lab A 에서 새 RX 패킷이 도착했을 때만 한 스텝 처리하고 송신/로그.
      - 새 패킷이 없으면 콜백은 조용히 대기(출력 없음).

    수신 신호 (VILLASnode → 이 코드, 4ch):
      [0] outdoor_temp     (°C)
      [1] solar_rad        (W/m²)
      [2] rel_humidity     (%)   ← 참고용
      [3] cooling_setpoint (°C)  ← Lab A 제어입력, 냉방 설정온도로 주입

    송신 신호 (이 코드 → VILLASnode, 2ch):
      [0] indoor_temp (°C)
      [1] hvac_power  (W)
    """

    # EMS 액추에이터 정의
    # (component_type, control_type, component_name)
    # ── IDF 에서 EnergyManagementSystem:Actuator 로 선언된 이름과 일치해야 함 ──
    ACT_OUTDOOR_TEMP = ("Weather Data", "Outdoor Dry Bulb", "Environment")
    ACT_SOLAR_DIFF   = ("Weather Data", "Diffuse Solar",    "Environment")
    # cooling_setpoint 주입용: Cooling 설정온도 스케줄을 액추에이터로 제어
    # IDF: Schedule:Constant, Cooling SP Sch, Any Number, 26.0;
    ACT_COOLING_SP   = ("Schedule:Constant", "Schedule Value", "Cooling SP Sch")

    # EnergyPlus Output Variable 정의
    # (variable_name, zone_or_component_name)
    VAR_INDOOR_TEMP   = ("Zone Mean Air Temperature",                      "ZONE ONE")
    # hvac_power 는 Ideal Loads 의 냉방 공급률(Cooling Rate)로 측정
    VAR_HVAC_POWER    = ("Zone Ideal Loads Supply Air Total Cooling Rate",  "ZONE ONE Ideal Loads")

    def __init__(
        self,
        idf_path:    str,
        epw_path:    str,
        local_port:  int   = 12001,     # [STEP5] VILLASnode -> 이 코드 수신 포트
        remote_host: str   = "192.168.239.159",
        remote_port: int   = 12003,     # [STEP6] 이 코드 -> VILLASnode 송신 포트
        zone_name:   str   = "ZONE ONE",
        log_interval: int  = 1,     # 새 값 처리 시 로그 출력 간격 (1=매번)
        debug_rx:    bool  = False,  # True → 수신 패킷 원시 바이트 로그
        idle_sleep:  float = 0.01,   # 새 값 없을 때 콜백이 쉬는 시간(초)
    ):
        self.idf_path    = os.path.abspath(idf_path)
        self.epw_path    = os.path.abspath(epw_path)
        self.local_port  = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.zone_name   = zone_name
        self.log_interval = log_interval
        self.debug_rx    = debug_rx
        self.idle_sleep  = idle_sleep
        self._first_rx_logged = False  # 첫 수신 패킷 원시 출력용 플래그

        # VILLASnode로부터 받은 최신 기상값 (공유)
        self._lock           = threading.Lock()
        self._outdoor_temp   = 25.0   # °C  초기값
        self._solar_rad      = 0.0    # W/m²
        self._rel_humidity   = 50.0   # %
        self._cooling_sp     = 26.0   # °C  Lab A 제어입력 초기값
        self._rx_cnt         = 0
        self._tx_seq         = 0
        self._ep_tick        = 0      # 실제 처리(송신)한 스텝 수

        # 이벤트 구동: 콜백이 "마지막으로 처리한 RX 카운터"를 기억.
        # 새 RX(_rx_cnt 증가)가 있을 때만 한 스텝 처리한다.
        self._processed_rx   = 0

        # EnergyPlus 핸들 (API ready 후 초기화)
        self._h_outdoor_temp  = -1
        self._h_solar_diff    = -1
        self._h_cooling_sp    = -1   # cooling_setpoint 주입용
        self._h_indoor_temp   = -1
        self._h_hvac_power    = -1
        self._handles_ready   = False

        self._running = False
        self._stop_event = threading.Event()  # Ctrl+C 즉시 종료용

        # 소켓
        self._recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._recv_sock.bind(("0.0.0.0", local_port))
        self._recv_sock.settimeout(0.5)
        self._send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # ── [STEP5] UDP 수신 루프 (별도 스레드) ──────────────────────────
    def _recv_loop(self):
        """
        [STEP5] VILLASnode → 이 코드 (소켓 수신):
          [0] outdoor_temp, [1] solar_rad, [2] rel_humidity, [3] cooling_setpoint
        수신 즉시 공유 변수에 저장 → EnergyPlus 콜백이 새 값 감지 후 처리.
        """
        logger.info("[STEP5 수신] 대기 ← 0.0.0.0:%d  (값이 오면 처리)", self.local_port)
        _parse_fail_cnt = 0
        while self._running:
            try:
                raw, addr = self._recv_sock.recvfrom(4096)

                # 첫 수신 패킷 또는 --debug-rx 시 원시 바이트 로그
                if self.debug_rx or not self._first_rx_logged:
                    logger.info(
                        "[RX-RAW] from=%s:%d  len=%d  hex=%s  text=%r",
                        addr[0], addr[1], len(raw),
                        raw[:32].hex(), raw[:80],
                    )
                    self._first_rx_logged = True

                result = parse_villas(raw)
                if result is None:
                    _parse_fail_cnt += 1
                    if _parse_fail_cnt <= 5 or _parse_fail_cnt % 100 == 0:
                        logger.warning(
                            "[STEP5 수신] 파싱 실패 #%d: from=%s:%d  raw=%r",
                            _parse_fail_cnt, addr[0], addr[1], raw[:80],
                        )
                    continue

                _parse_fail_cnt = 0
                seq, vals = result

                if len(vals) < 2:
                    logger.warning(
                        "[STEP5 수신] 채널 부족 (받음=%d, 최소=2): vals=%s", len(vals), vals
                    )
                    continue

                with self._lock:
                    self._outdoor_temp = vals[0]
                    self._solar_rad    = vals[1]
                    if len(vals) >= 3:
                        self._rel_humidity = vals[2]
                    if len(vals) >= 4:
                        self._cooling_sp = vals[3]   # Lab A 제어입력
                    self._rx_cnt += 1     # ← 콜백이 이 증가를 감지해 처리
                    rx_cnt = self._rx_cnt

                logger.info(
                    "[Lab C | STEP5 수신] VILLAS → Lab C  seq=%d | 외기=%.2f°C  일사=%.1fW/m²  습도=%.1f%%  설정온도=%.2f°C",
                    seq, vals[0], vals[1],
                    vals[2] if len(vals) >= 3 else self._rel_humidity,
                    vals[3] if len(vals) >= 4 else self._cooling_sp,
                )
            except socket.timeout:
                continue
            except OSError as e:
                if self._running:
                    logger.error("[STEP5 수신] 오류: %s", e)

    # ── EnergyPlus 핸들 초기화 ───────────────────────────────────────
    def _init_handles(self, state):
        """API 준비 완료 후 최초 1회 핸들을 가져온다."""
        if self._handles_ready:
            return

        api = self._api

        # ── 액추에이터 핸들 (기상값 주입용) ──────────────────────────
        self._h_outdoor_temp = api.exchange.get_actuator_handle(
            state,
            self.ACT_OUTDOOR_TEMP[0],
            self.ACT_OUTDOOR_TEMP[1],
            self.ACT_OUTDOOR_TEMP[2],
        )
        self._h_solar_diff = api.exchange.get_actuator_handle(
            state,
            self.ACT_SOLAR_DIFF[0],
            self.ACT_SOLAR_DIFF[1],
            self.ACT_SOLAR_DIFF[2],
        )
        # cooling_setpoint 주입용 액추에이터 (Lab A 제어입력 -> EP 냉방 설정온도)
        self._h_cooling_sp = api.exchange.get_actuator_handle(
            state,
            self.ACT_COOLING_SP[0],
            self.ACT_COOLING_SP[1],
            self.ACT_COOLING_SP[2],
        )

        # ── 출력 변수 핸들 (실내온도, HVAC 전력) ──────────────────────
        self._h_indoor_temp = api.exchange.get_variable_handle(
            state,
            self.VAR_INDOOR_TEMP[0],
            self.zone_name,
        )
        self._h_hvac_power = api.exchange.get_variable_handle(
            state,
            self.VAR_HVAC_POWER[0],
            self.VAR_HVAC_POWER[1],   # equipment name, not zone name
        )

        logger.info("[핸들] outdoor_temp  actuator = %d %s",
                    self._h_outdoor_temp, "OK" if self._h_outdoor_temp >= 0 else "FAIL")
        logger.info("[핸들] solar_diff    actuator = %d %s",
                    self._h_solar_diff,   "OK" if self._h_solar_diff   >= 0 else "FAIL")
        logger.info("[핸들] cooling_sp    actuator = %d %s",
                    self._h_cooling_sp,   "OK" if self._h_cooling_sp   >= 0 else "FAIL")
        logger.info("[핸들] indoor_temp   variable = %d %s",
                    self._h_indoor_temp,  "OK" if self._h_indoor_temp  >= 0 else "FAIL")
        logger.info("[핸들] hvac_power    variable = %d %s",
                    self._h_hvac_power,   "OK" if self._h_hvac_power   >= 0 else "FAIL")

        if self._h_outdoor_temp < 0:
            logger.warning("[핸들] outdoor_temp 액추에이터 없음 → IDF EMS 선언 확인")
        if self._h_solar_diff < 0:
            logger.warning("[핸들] solar_diff 액추에이터 없음 → IDF EMS 또는 Schedule 선언 확인")
        if self._h_cooling_sp < 0:
            logger.warning("[핸들] cooling_sp 액추에이터 없음 → IDF 'Cooling SP Sch' Schedule:Compact 확인")
        if self._h_indoor_temp < 0:
            logger.warning("[핸들] indoor_temp 변수 없음 → IDF Output:Variable 확인")
        if self._h_hvac_power < 0:
            logger.warning("[핸들] hvac_power 변수 없음 → IDF Output:Variable 확인")

        self._handles_ready = True

    # ── [STEP5] EnergyPlus 콜백: 타임스텝 시작 — 기상값/제어입력 주입 ──
    def _on_begin_timestep(self, state):
        """
        매 타임스텝 시작 시 호출. (EnergyPlus 연산의 입력 주입 단계)

        이벤트 구동:
          - 새 RX 패킷이 없으면 잠시 대기(busy-wait 방지)하고 그냥 리턴.
            → 같은 기상값으로 EnergyPlus 가 폭주하는 것을 막는다.
          - Ctrl+C(_stop_event) 시 즉시 리턴해 EnergyPlus 종료를 유도.
        """
        if not self._api.exchange.api_data_fully_ready(state):
            return
        self._init_handles(state)

        # ── 새 값이 올 때까지 이 스텝을 잡아둔다(이벤트 구동의 핵심) ──
        while not self._stop_event.is_set():
            with self._lock:
                has_new = self._rx_cnt > self._processed_rx
            if has_new:
                break
            time.sleep(self.idle_sleep)   # 조용히 대기 (출력 없음)

        if self._stop_event.is_set():
            return  # 종료 중 — 주입하지 않고 빠져나감

        with self._lock:
            outdoor = self._outdoor_temp
            solar   = self._solar_rad
            cool_sp = self._cooling_sp

        # 외기온도 주입
        if self._h_outdoor_temp >= 0:
            self._api.exchange.set_actuator_value(
                state, self._h_outdoor_temp, outdoor
            )

        # 일사량 주입 (W/m² → EnergyPlus는 W/m² 단위 그대로 사용)
        if self._h_solar_diff >= 0:
            self._api.exchange.set_actuator_value(
                state, self._h_solar_diff, solar
            )

        # cooling_setpoint 주입 (Lab A 제어입력 → EP 냉방 설정온도) — 폐루프 핵심
        if self._h_cooling_sp >= 0:
            self._api.exchange.set_actuator_value(
                state, self._h_cooling_sp, cool_sp
            )

    # ── [STEP5/6] EnergyPlus 콜백: 타임스텝 종료 — 결과 읽기 + 송신 ──
    def _on_end_timestep(self, state):
        """
        매 타임스텝 종료 후 호출.
          [STEP5] EnergyPlus 계산 결과(실내온도, HVAC전력)를 읽음
          [STEP6] 읽은 값을 소켓(UDP)을 통해 VILLASnode로 송신

        이벤트 구동:
          - 이 스텝이 '새 값으로 처리된' 스텝일 때만 송신/로그.
            (begin 콜백에서 새 값을 기다렸으므로 여기 도달 = 새 값 처리됨)
          - 종료 중이면 아무것도 하지 않음.

        송신 신호:
          [0] indoor_temp   (°C)
          [1] cooling_load  (W)
        """
        if not self._api.exchange.api_data_fully_ready(state):
            return
        if self._stop_event.is_set():
            return
        self._init_handles(state)

        # 새 RX 가 없으면(= begin 이 종료로 빠져나온 경우) 처리하지 않음
        with self._lock:
            if self._rx_cnt <= self._processed_rx:
                return
            self._processed_rx = self._rx_cnt   # 이 RX 를 소비 처리
            outdoor = self._outdoor_temp
            solar   = self._solar_rad
            rh      = self._rel_humidity
            cool_sp = self._cooling_sp

        self._ep_tick += 1

        # [STEP5] 실내온도 읽기 (EnergyPlus 계산 결과)
        indoor_temp = 24.0   # 기본값 (핸들 없을 때)
        if self._h_indoor_temp >= 0:
            indoor_temp = self._api.exchange.get_variable_value(
                state, self._h_indoor_temp
            )

        # [STEP5] HVAC 전력(냉방 공급률) 읽기 (EnergyPlus 계산 결과)
        hvac_power = 0.0
        if self._h_hvac_power >= 0:
            hvac_power = self._api.exchange.get_variable_value(
                state, self._h_hvac_power
            )

        # [STEP6] VILLASnode로 소켓(UDP) 송신 (2채널: indoor_temp, hvac_power)
        payload = make_villas([indoor_temp, hvac_power], self._tx_seq)
        self._send_sock.sendto(payload, (self.remote_host, self.remote_port))
        self._tx_seq += 1

        # 로그 (새 값 처리 시에만, log_interval 간격으로)
        if self._ep_tick % self.log_interval == 0:
            logger.info(
                "[Lab C | STEP5 계산] EnergyPlus 연산 → 실내온도=%.2f°C  HVAC전력=%.1fW  (설정온도=%.2f°C)",
                indoor_temp, hvac_power, cool_sp,
            )
            logger.info(
                "[Lab C | STEP6 전송] Lab C → VILLAS(%s:%d)  2ch 전송",
                self.remote_host, self.remote_port,
            )

    # ── ExternalInterface 블록 제거 ────────────────────────────────
    def _make_api_idf(self, src: str, dst: str) -> None:
        """
        IDF 에서 ExternalInterface* 블록을 모두 제거해 dst 에 저장.
        ExternalInterface:FunctionalMockupUnitExport 블록이 있으면
        standalone Python API 모드에서 EP 가 FMU 소켓을 찾다가 code=1 로 종료함.
        """
        in_block = False
        removed  = []
        out_lines: list = []
        with open(src, encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not in_block:
                    if (stripped.upper().startswith("EXTERNALINTERFACE")
                            and not stripped.startswith("!")):
                        in_block = True
                        removed.append(stripped[:60])
                if in_block:
                    code = stripped.split("!")[0]
                    if ";" in code:
                        in_block = False
                    continue
                out_lines.append(line)
        if removed:
            logger.info("[IDF] ExternalInterface 블록 제거 → API 모드 호환 임시 IDF")
            for r in removed:
                logger.info("      제거: %s", r)
        with open(dst, "w", encoding="utf-8") as f:
            f.writelines(out_lines)

    # ── 실행 ────────────────────────────────────────────────────────
    def run(self):
        logger.info("=" * 60)
        logger.info(" Lab C — EnergyPlus Socket Sim (STEP5 수신/계산, STEP6 송신)")
        logger.info("  IDF    : %s", self.idf_path)
        logger.info("  EPW    : %s", self.epw_path)
        logger.info("  RX     : 0.0.0.0:%d  (VILLASnode → EP) [STEP4->5]", self.local_port)
        logger.info("  TX     : %s:%d  (EP → VILLASnode) [STEP6->7]", self.remote_host, self.remote_port)
        logger.info("  Zone   : %s", self.zone_name)
        logger.info("  모드   : 값이 올 때만 처리/출력, 없으면 대기")
        logger.info("=" * 60)

        self._running = True

        # [STEP5] 수신 스레드 시작
        rx_thread = threading.Thread(
            target=self._recv_loop, daemon=True, name="villas-rx"
        )
        rx_thread.start()

        # EnergyPlus 실행
        self._api = EnergyPlusAPI()
        state     = self._api.state_manager.new_state()

        # 콜백 등록
        self._api.runtime.callback_begin_system_timestep_before_predictor(
            state, self._on_begin_timestep
        )
        self._api.runtime.callback_end_zone_timestep_after_zone_reporting(
            state, self._on_end_timestep
        )

        # EnergyPlus 콘솔 출력 억제
        self._api.runtime.set_console_output_status(state, False)

        # 출력 디렉터리
        out_dir = os.path.join(os.path.dirname(self.idf_path), "ep_output")
        os.makedirs(out_dir, exist_ok=True)

        # ExternalInterface 블록 제거 → API 모드 호환 임시 IDF 생성
        api_idf = os.path.join(out_dir, "_api_run.idf")
        self._make_api_idf(self.idf_path, api_idf)

        ret = -1
        try:
            with _ep_quiet():
                ret = self._api.runtime.run_energyplus(state, [
                    "-w", self.epw_path,
                    "-d", out_dir,
                    api_idf,
                ])
        except KeyboardInterrupt:
            logger.info("[종료] Ctrl+C 감지 — 종료 중...")
            self._stop_event.set()
            self._running = False
        finally:
            self._stop_event.set()
            self._running = False

        logger.info(
            "EnergyPlus 종료 | code=%d  처리=%d  TX=%d  RX=%d",
            ret, self._ep_tick, self._tx_seq, self._rx_cnt,
        )
        try:
            self._api.state_manager.delete_state(state)
        except Exception:
            pass
        self._recv_sock.close()
        self._send_sock.close()


# ─────────────────────────────────────────────────────────────────────
# 진입점
# ─────────────────────────────────────────────────────────────────────
def main():
    p = argparse.ArgumentParser(
        description="Lab C — EnergyPlus + VILLASnode 소켓 시뮬레이터 (이벤트 구동, STEP5/6)"
    )
    p.add_argument("idf",  help="IDF 파일 경로")
    p.add_argument("epw",  help="EPW 기상 파일 경로")
    p.add_argument("--local-port",   type=int, default=12001,
                   help="VILLASnode 수신 포트 (기본 12001)")
    p.add_argument("--remote-host",  default="192.168.239.159",
                   help="VILLASnode 서버 IP (기본 192.168.239.159)")
    p.add_argument("--remote-port",  type=int, default=12003,
                   help="VILLASnode 수신 포트 (기본 12003)")
    p.add_argument("--zone",         default="ZONE ONE",
                   help="EnergyPlus Zone 이름 (기본 'ZONE ONE')")
    p.add_argument("--ep-dir",       default=None,
                   help="EnergyPlus 설치 경로 (기본 /usr/local/EnergyPlus-25-2-0)")
    p.add_argument("--log-interval", type=int, default=1,
                   help="새 값 처리 시 로그 출력 간격 (기본 1=매번)")
    p.add_argument("--debug-rx", action="store_true",
                   help="수신 패킷 원시 바이트를 매번 로그 출력 (형식 확인용)")
    args = p.parse_args()

    if args.ep_dir:
        global EP_DIR
        EP_DIR = args.ep_dir

    if not os.path.exists(args.idf):
        print(f"[FAIL] IDF 파일 없음: {args.idf}")
        sys.exit(1)
    if not os.path.exists(args.epw):
        print(f"[FAIL] EPW 파일 없음: {args.epw}")
        sys.exit(1)

    sim = EPSocketSim(
        idf_path     = args.idf,
        epw_path     = args.epw,
        local_port   = args.local_port,
        remote_host  = args.remote_host,
        remote_port  = args.remote_port,
        zone_name    = args.zone,
        log_interval = args.log_interval,
        debug_rx     = args.debug_rx,
    )
    try:
        sim.run()
    except KeyboardInterrupt:
        sim._stop_event.set()
        sim._running = False
        logger.info("[종료] 완료")


if __name__ == "__main__":
    main()
