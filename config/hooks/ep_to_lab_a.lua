-- =====================================================================
-- [STEP 7] Hook: Lab B (EnergyPlus) -> Lab A
--
--   9단계 파이프라인 중 [7]단계: "받은 값을 Lab A가 이해할 수 있는
--   포맷으로 변경"에 해당.
--
--   역할:
--     - EnergyPlus가 계산한 2채널 결과값을 Lab A 수신 규격으로 정제
--     - 범위 이탈/NaN 값은 직전 정상값으로 유지 (통신 튐 방어)
--     - 송수신 값 로그 출력
--
-- smp.data 인덱스 (VILLASnode 0-based):
--   [0] indoor_temp (degC)
--   [1] hvac_power  (W)   <- EP가 계산한 냉방전력 (폐루프 피드백)
-- =====================================================================

local DEST = "LabA(192.168.239.185:12011)"

local SPEC = {
    { lo = -10, hi = 60,    def = 24, name = "indoor_temp" },
    { lo =   0, hi = 1e6,   def =  0, name = "hvac_power"  },
}

local last = {}
local cnt  = 0

function start()
    cnt = 0
    for i = 1, #SPEC do last[i] = SPEC[i].def end
    info("[B1->A][STEP7] start | dest=" .. DEST)
end

local function safe(v, sp, i)
    if v == nil or v ~= v then
        warn(string.format("[B1->A] %s nil/NaN -> prev=%.2f", sp.name, last[i]))
        return last[i]
    end
    if v < sp.lo or v > sp.hi then
        warn(string.format("[B1->A] %s=%.2f 범위초과 -> prev=%.2f", sp.name, v, last[i]))
        return last[i]
    end
    return v
end

-- [STEP 7] 실제 변환 처리 — VILLASnode가 각 샘플마다 호출
function process(smp)
    if smp == nil or smp.data == nil then return 0 end
    cnt = cnt + 1

    -- [STEP 6] EnergyPlus -> VILLASnode 로 도착한 원본 계산값 로그
    info(string.format(
        "[VILLAS | 전달][STEP6->7] Lab B -> VILLAS | seq=%d | indoor=%.2f  hvac_power=%.1fW",
        smp.sequence or -1,
        smp.data[0] or 0, smp.data[1] or 0))

    -- [STEP 7] 값 정제 (Lab A 전달 전 안전범위 적용)
    for i = 1, #SPEC do
        smp.data[i - 1] = safe(smp.data[i - 1], SPEC[i], i)
        last[i]         = smp.data[i - 1]
    end

    -- [STEP 8 예고] VILLASnode -> Lab A 로 나갈 값 로그
    info(string.format(
        "[VILLAS | 전달][STEP7->8] VILLAS -> Lab A (%s) | indoor=%.2f  hvac_power=%.1fW",
        DEST, smp.data[0], smp.data[1]))

    return 0
end
