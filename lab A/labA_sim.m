function labA_sim(varargin)
% LABA_SIM  Lab A 시뮬레이터 (HILS 9단계 폐루프, MATLAB)
%
%   담당 단계:
%     [1] 임의의 외란값 생성 (외기온도/일사량/습도) + 제어입력(설정온도) 계산
%     [2] 소켓(UDP) 기반으로 VILLASnode에 전송
%     [8] VILLASnode로부터 소켓(UDP) 기반으로 피드백 수신
%     [9] 받은 값(실내온도/HVAC전력)을 실제로 반영하여 다음 값 생성
%
%   ※ 포맷: villas.human (텍스트) — hils.conf 의 lab_a 노드와 일치
%      와이어 형식:  <sec>.<nsec>(<seq>)\t<v1>\t<v2>\t...\n
%
%   입출력 채널 (폐루프 규격):
%     [2] 송신 (Lab A -> VILLAS, 4채널):
%        x(1)=outdoor_temp[degC]  x(2)=solar_rad[W/m2]
%        x(3)=rel_humidity[%]     x(4)=cooling_setpoint[degC]  <- 제어입력
%     [8] 수신 (VILLAS -> Lab A, 2채널):
%        y(1)=indoor_temp[degC]   y(2)=hvac_power[W]
%
%   동작 (송신-구동, Period 주기):
%     (1) [STEP1] 데이터 생성 (직전 피드백 반영한 setpoint 포함)
%     (2) [STEP2] VILLAS 로 송신  -> (VILLAS -> Lab B(EnergyPlus) -> VILLAS)
%     (3) [STEP8] 피드백 수신 대기 (최대 1주기)
%     (4) [STEP9] 받은 indoor/hvac 를 반영 -> 다음 setpoint 계산에 사용
%     --- Period 초 후 loop 반복 ---
%
%   사용 예:
%     labA_sim
%     labA_sim('RemoteHost','192.168.239.159', ...
%              'SendPort',12010,'RecvPort',12011,'Period',3.0)
%
%   종료: Ctrl+C
% -------------------------------------------------------------------------

    % ---- 파라미터 ----
    p = inputParser;
    addParameter(p, 'RemoteHost', '192.168.239.159');   % VILLASnode 서버 IP
    addParameter(p, 'SendPort',   12010);               % [STEP2] TX (LabA->VILLAS lab_a.in)
    addParameter(p, 'RecvPort',   12011);               % [STEP8] RX (VILLAS->LabA lab_a.out)
    addParameter(p, 'Period',     3.0);                 % 루프 주기 [s]
    addParameter(p, 'Verbose',    true);
    parse(p, varargin{:});
    cfg = p.Results;

    % 피드백 수신 타임아웃 = 주기와 동일 (ms)
    recvTimeoutMs = round(cfg.Period * 1000);

    % ---- [Java 담당] UDP 소켓 열기 ----
    io = udp_open(cfg.RecvPort, cfg.RemoteHost, cfg.SendPort, recvTimeoutMs);
    cleanupObj = onCleanup(@() udp_close(io)); %#ok<NASGU>

    if cfg.Verbose
        fprintf('%s\n', repmat('=', 1, 60));
        fprintf(' Lab A - Data Generator + HVAC Controller (9단계 HILS 폐루프)\n');
        fprintf('  [STEP2] TX -> VILLAS %s:%d   [STEP8] RX <- :%d\n', ...
            cfg.RemoteHost, cfg.SendPort, cfg.RecvPort);
        fprintf('  루프 주기: %.1f초\n', cfg.Period);
        fprintf('  종료하려면 Ctrl+C\n');
        fprintf('%s\n', repmat('=', 1, 60));
    end

    % ---- 제어기/생성기 상태 ----
    ctrl = controller_init();   % HVAC 제어기 상태 (setpoint, 피드백)
    gen_t0 = posixtime(datetime('now','TimeZone','UTC'));  % 외란 위상 기준시각

    seq    = uint32(0);
    loop   = 0;
    rx_cnt = 0;

    % ===================== 메인 폐루프 =====================
    while true
        loop = loop + 1;
        t_loop = tic;

        fprintf('\n');
        fprintf('══════════ Loop #%d ══════════\n', loop);

        % ---- [STEP9-전] 직전 피드백 기반 제어 반영 로그 ----
        if ~isempty(ctrl.indoor_temp)
            fprintf('[Lab A | STEP9 반영] 직전 피드백 실내=%.2f°C  HVAC=%.1fW 기반으로 제어 계산\n', ...
                ctrl.indoor_temp, ctrl.hvac_power);
        else
            fprintf('[Lab A | STEP9 반영] 첫 사이클 — 초기 설정온도로 시작\n');
        end

        % ---- [STEP1] 임의값(외란) 생성 + 제어입력 계산 ----
        dist = generate_disturbance(gen_t0);          % [outdoor, solar, rh]
        [ctrl, setpoint] = controller_compute(ctrl);   % 제어입력 setpoint
        values_out = [dist(1), dist(2), dist(3), setpoint];

        fprintf('[Lab A | STEP1 생성] 외기=%.2f°C  일사=%.1fW/m²  습도=%.1f%%  설정온도=%.2f°C\n', ...
            dist(1), dist(2), dist(3), setpoint);

        % ---- [STEP2] 인코드 + 소켓(UDP) 송신 -> VILLASnode ----
        seq    = seq + 1;
        txRaw  = villas_human_encode(values_out, seq);
        udp_send(io, txRaw);
        fprintf('[Lab A | STEP2 송신] → VILLAS(%s:%d)  4ch 전송\n', cfg.RemoteHost, cfg.SendPort);

        % ---- [STEP8] 피드백 수신 대기 (최대 1 주기) ----
        raw = udp_recv(io);                 % uint8 또는 [] (타임아웃)
        if ~isempty(raw)
            [values_in, seq_in] = villas_human_decode(raw);
            if numel(values_in) >= 2
                rx_cnt = rx_cnt + 1;
                % ---- [STEP9] 피드백 반영 ----
                ctrl = controller_update(ctrl, values_in(1), values_in(2));
                fprintf('[Lab A | STEP8 수신] VILLAS ← Lab B  seq=%u | 실내온도=%.2f°C  HVAC전력=%.1fW\n', ...
                    seq_in, values_in(1), values_in(2));
            else
                fprintf('[Lab A | STEP8 수신] 채널 부족 — 무시\n');
            end
        else
            fprintf('[Lab A | STEP8 대기] 이번 주기 내 피드백 미도착 (다음 루프에서 반영)\n');
        end

        % ---- 정확히 Period 주기 유지 ----
        elapsed = toc(t_loop);
        remain  = cfg.Period - elapsed;
        if remain > 0
            fprintf('[Lab A | 대기] %.1f초 후 다음 루프...\n', remain);
            pause(remain);
        end
    end
end


% =========================================================================
%  [STEP1] 외란 생성기
% =========================================================================
function d = generate_disturbance(t0)
% GENERATE_DISTURBANCE  [outdoor_temp, solar_rad, rel_humidity] 임의값 생성
    t     = posixtime(datetime('now','TimeZone','UTC'));
    phase = ((t - t0) / 60.0) * 2 * pi;

    outdoor = 28.0 + 6.0 * sin(phase) + (rand*1.0 - 0.5);
    solar   = max(0.0, 600.0 + 400.0 * sin(phase) + (rand*60 - 30));
    humid   = max(20.0, min(95.0, 55.0 + 20.0 * cos(phase) + (rand*6 - 3)));

    d = [ round(outdoor, 2), round(solar, 1), round(humid, 1) ];
end


% =========================================================================
%  [STEP1/9] HVAC 제어기 — 제어입력 계산(1) 및 피드백 반영(9)
% =========================================================================
function c = controller_init()
% 제어기 상태 구조체 초기화
    c = struct();
    c.COMFORT  = 26.0;     % 목표 실내온도
    c.KP       = 0.4;      % 비례 게인
    c.POWER_TH = 3000.0;   % 전력 완화 임계
    c.POWER_KP = 0.0005;
    c.SP_MIN   = 22.0;
    c.SP_MAX   = 28.0;
    c.setpoint = 26.0;     % 초기 setpoint
    c.indoor_temp = [];    % 피드백 (빈값=미수신)
    c.hvac_power  = [];
end

function c = controller_update(c, indoor_temp, hvac_power)
% [STEP9] EP 피드백 반영 (Plant -> Controller)
    c.indoor_temp = indoor_temp;
    c.hvac_power  = hvac_power;
end

function [c, sp] = controller_compute(c)
% [STEP1] 다음 제어입력 계산 (Controller -> Plant)
    if ~isempty(c.indoor_temp)
        err = c.indoor_temp - c.COMFORT;
        sp  = c.COMFORT - c.KP * err;
        if ~isempty(c.hvac_power) && c.hvac_power > c.POWER_TH
            sp = sp + c.POWER_KP * (c.hvac_power - c.POWER_TH);
        end
        sp = max(c.SP_MIN, min(c.SP_MAX, sp));
        c.setpoint = sp;
    end
    sp = round(c.setpoint, 2);
end


% =========================================================================
%  [STEP2/8] Java 담당 : UDP 소켓 입출력 헬퍼
% =========================================================================
function io = udp_open(localPort, remoteHost, remotePort, timeoutMs)
    import java.net.DatagramSocket
    import java.net.InetAddress
    import java.net.InetSocketAddress

    rx = DatagramSocket([]);
    rx.setReuseAddress(true);
    rx.bind(InetSocketAddress(localPort));     % 0.0.0.0:localPort
    rx.setSoTimeout(timeoutMs);

    tx = DatagramSocket([]);

    io = struct();
    io.rx         = rx;
    io.tx         = tx;
    io.txAddr     = InetAddress.getByName(remoteHost);
    io.remotePort = remotePort;
    io.BUFLEN     = 1500;
    io.packet     = java.net.DatagramPacket(zeros(1, io.BUFLEN, 'int8'), io.BUFLEN);
end

function raw = udp_recv(io)
    % [STEP8] VILLASnode -> Lab A 수신
    raw = [];
    io.packet.setLength(io.BUFLEN);
    try
        io.rx.receive(io.packet);
    catch ME
        if contains(ME.message, 'SocketTimeoutException')
            return;
        else
            rethrow(ME);
        end
    end
    n   = io.packet.getLength();
    b   = typecast(io.packet.getData(), 'uint8');
    raw = b(1:n);
end

function udp_send(io, raw)
    % [STEP2] Lab A -> VILLASnode 송신
    txBytes  = typecast(uint8(raw), 'int8');
    txPacket = java.net.DatagramPacket(txBytes, numel(txBytes), ...
                                       io.txAddr, io.remotePort);
    io.tx.send(txPacket);
end

function udp_close(io)
    try, io.rx.close(); catch, end
    try, io.tx.close(); catch, end
    fprintf('\n[Lab A] 소켓 정리 완료. 종료.\n');
end


% =========================================================================
%  [STEP2/8] villas.human 인코드/디코드
% =========================================================================
function [values, seq] = villas_human_decode(bytes)
    values = [];
    seq    = 0;

    s = strtrim(native2unicode(bytes(:).', 'UTF-8'));
    if isempty(s) || s(1) == '#'
        return;
    end

    lines = strsplit(s, sprintf('\n'));
    line  = '';
    for i = numel(lines):-1:1
        t = strtrim(lines{i});
        if ~isempty(t) && t(1) ~= '#'
            line = t;
            break;
        end
    end
    if isempty(line)
        return;
    end

    lp = strfind(line, '(');
    rp = strfind(line, ')');
    if ~isempty(lp) && ~isempty(rp) && rp(1) > lp(1)
        seq = str2double(line(lp(1)+1 : rp(1)-1));
        if isnan(seq), seq = 0; end
        data_part = strtrim(line(rp(1)+1 : end));
    else
        data_part = line;
    end

    data_part = strrep(data_part, sprintf('\t'), ' ');
    toks = strsplit(strtrim(data_part));
    out = [];
    for i = 1:numel(toks)
        tk = toks{i};
        if isempty(tk), continue; end
        eq = strfind(tk, '=');
        if ~isempty(eq), tk = tk(eq(1)+1:end); end
        v = str2double(tk);
        if ~isnan(v)
            out(end+1) = v; %#ok<AGROW>
        end
    end
    values = out;
end

function bytes = villas_human_encode(values, seq)
    ts    = now_ts();
    parts = cell(1, numel(values));
    for i = 1:numel(values)
        parts{i} = sprintf('%.6f', values(i));
    end
    body = strjoin(parts, sprintf('\t'));
    line = sprintf('%u.%09u(%u)\t%s\n', ts(1), ts(2), seq, body);
    bytes = unicode2native(line, 'UTF-8');
end

function ts = now_ts()
    t    = posixtime(datetime('now','TimeZone','UTC'));
    sec  = floor(t);
    nsec = round((t - sec) * 1e9);
    if nsec >= 1e9, nsec = 999999999; end
    ts = [uint32(sec), uint32(nsec)];
end
