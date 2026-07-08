-- =====================================================================
-- Hook: Lab C(EnergyPlus) -> WebRTC(Site A)
-- 안전범위/hold-last 로직은 기존 ep_to_lab_a.lua 와 동일. 목적지만 WebRTC.
-- =====================================================================

local DEST = "WebRTC(webrtc_c -> Site A)"

local SPEC = {
    { lo = -10, hi = 60,    def = 24, name = "indoor_temp" },
    { lo =   0, hi = 1e6,   def =  0, name = "hvac_power"  },
}

local last = {}
local cnt  = 0

function start()
    cnt = 0
    for i = 1, #SPEC do last[i] = SPEC[i].def end
    info("[C->WebRTC][start] dest=" .. DEST)
end

local function safe(v, sp, i)
    if v == nil or v ~= v then
        warn(string.format("[C->WebRTC] %s nil/NaN -> prev=%.2f", sp.name, last[i]))
        return last[i]
    end
    if v < sp.lo or v > sp.hi then
        warn(string.format("[C->WebRTC] %s=%.2f 범위초과 -> prev=%.2f", sp.name, v, last[i]))
        return last[i]
    end
    return v
end

function process(smp)
    if smp == nil or smp.data == nil then return 0 end
    cnt = cnt + 1

    info(string.format(
        "[VILLAS | GW-C] LabC -> GW-C | seq=%d | indoor=%.2f hvac_power=%.1fW",
        smp.sequence or -1, smp.data[0] or 0, smp.data[1] or 0))

    for i = 1, #SPEC do
        smp.data[i - 1] = safe(smp.data[i - 1], SPEC[i], i)
        last[i]         = smp.data[i - 1]
    end

    info(string.format(
        "[VILLAS | GW-C] GW-C -> %s | indoor=%.2f hvac_power=%.1fW",
        DEST, smp.data[0], smp.data[1]))

    return 0
end
