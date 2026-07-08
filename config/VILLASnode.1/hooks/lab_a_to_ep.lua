-- =====================================================================
-- [STEP 3] Hook: Lab A -> Lab B (EnergyPlus)
--
--   9단계 파이프라인 중 [3]단계: "받은 값을 EnergyPlus에 줄 수 있게
--   hook 기반으로 변경"에 해당.
--
--   역할:
--     - Lab A가 보낸 4채널 값을 EnergyPlus 주입 안전범위로 정제(clamp)
--     - nil/NaN 방어 (직전 정상값으로 유지)
--     - 송수신 값 로그 출력
--
-- smp.data 인덱스 (VILLASnode 0-based, use_names=false):
--   [0] outdoor_temp     (degC)
--   [1] solar_rad        (W/m^2)
--   [2] rel_humidity     (%)
--   [3] cooling_setpoint (degC)  <- HVAC 제어입력 (폐루프 핵심)
-- =====================================================================

local DEST = "EnergyPlus(192.168.239.185:12001)"

-- 안전 범위 [lo, hi, default, name]
local SPEC = {
    { lo = -50, hi = 60,   def = 25, name = "outdoor_temp"     },
    { lo =   0, hi = 1400, def =  0, name = "solar_rad"        },
    { lo =   0, hi = 100,  def = 50, name = "rel_humidity"     },
    { lo =  18, hi = 30,   def = 26, name = "cooling_setpoint" },
}

local last = {}
local cnt  = 0

function start()
    cnt = 0
    for i = 1, #SPEC do last[i] = SPEC[i].def end
    info("[A->B1][STEP3] start | dest=" .. DEST)
end

local function clamp(v, sp, i)
    if v == nil or v ~= v then
        warn(string.format("[A->B1] %s nil/NaN -> prev=%.2f", sp.name, last[i]))
        return last[i]
    end
    if v < sp.lo then
        warn(string.format("[A->B1] %s=%.2f < %.0f -> clamp", sp.name, v, sp.lo))
        return sp.lo
    end
    if v > sp.hi then
        warn(string.format("[A->B1] %s=%.2f > %.0f -> clamp", sp.name, v, sp.hi))
        return sp.hi
    end
    return v
end

-- [STEP 3] 실제 변환 처리 — VILLASnode가 각 샘플마다 호출
function process(smp)
    if smp == nil or smp.data == nil then return 0 end
    cnt = cnt + 1

    -- [STEP 2] Lab A -> VILLASnode 로 도착한 원본값 로그
    info(string.format(
        "[VILLAS | 전달][STEP2->3] Lab A -> VILLAS | seq=%d | outdoor=%.2f  solar=%.1f  rh=%.1f  setpoint=%.2f",
        smp.sequence or -1,
        smp.data[0] or 0, smp.data[1] or 0, smp.data[2] or 0, smp.data[3] or 0))

    -- [STEP 3] 값 정제 (EnergyPlus 주입 전 안전범위 적용)
    for i = 1, #SPEC do
        smp.data[i - 1] = clamp(smp.data[i - 1], SPEC[i], i)
        last[i]         = smp.data[i - 1]
    end

    -- [STEP 4 예고] VILLASnode -> EnergyPlus 로 나갈 값 로그
    info(string.format(
        "[VILLAS | 전달][STEP3->4] VILLAS -> Lab B (%s) | outdoor=%.2f  solar=%.1f  rh=%.1f  setpoint=%.2f",
        DEST, smp.data[0], smp.data[1], smp.data[2], smp.data[3]))

    return 0
end
