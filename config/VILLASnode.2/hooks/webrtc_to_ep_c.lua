-- =====================================================================
-- Hook: WebRTC(Site A) -> Lab C(EnergyPlus)
-- clamp 로직은 기존 lab_a_to_ep.lua 와 동일. 목적지만 Lab C.
-- =====================================================================

local DEST = "LabC-EnergyPlus(ep_c)"

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
    info("[WebRTC->C][start] dest=" .. DEST)
end

local function clamp(v, sp, i)
    if v == nil or v ~= v then
        warn(string.format("[WebRTC->C] %s nil/NaN -> prev=%.2f", sp.name, last[i]))
        return last[i]
    end
    if v < sp.lo then return sp.lo end
    if v > sp.hi then return sp.hi end
    return v
end

function process(smp)
    if smp == nil or smp.data == nil then return 0 end
    cnt = cnt + 1

    info(string.format(
        "[VILLAS | GW-C] WebRTC -> GW-C | seq=%d | outdoor=%.2f solar=%.1f rh=%.1f setpoint=%.2f",
        smp.sequence or -1, smp.data[0] or 0, smp.data[1] or 0, smp.data[2] or 0, smp.data[3] or 0))

    for i = 1, #SPEC do
        smp.data[i - 1] = clamp(smp.data[i - 1], SPEC[i], i)
        last[i]         = smp.data[i - 1]
    end

    info(string.format(
        "[VILLAS | GW-C] GW-C -> %s | outdoor=%.2f solar=%.1f rh=%.1f setpoint=%.2f",
        DEST, smp.data[0], smp.data[1], smp.data[2], smp.data[3]))

    return 0
end
