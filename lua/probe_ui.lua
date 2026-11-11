-- exec=tt
-- Probe 3: enumerate the XLua wrapper table of RPG.Client.UIManager.
local function log(msg)
    CS.UnityEngine.Debug.Log("[dsh] " .. tostring(msg))
end

local ok, err = pcall(function()
    local t = CS.RPG.Client.UIManager
    local keys = {}
    local n = 0
    for k, _ in pairs(t) do
        n = n + 1
        keys[#keys + 1] = tostring(k)
    end
    log("staticCount=" .. n)
    table.sort(keys)
    log("static=" .. table.concat(keys, ","))

    local inst = t.Instance
    local mt = getmetatable(inst)
    log("instanceMt=" .. tostring(mt))
    local ik = {}
    if type(mt) == "table" then
        for k, _ in pairs(mt) do ik[#ik + 1] = tostring(k) end
        table.sort(ik)
        log("instanceMtKeys=" .. table.concat(ik, ","))
    end
end)
if not ok then
    log("probe3-error=" .. tostring(err))
end
