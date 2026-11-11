-- exec=tt
-- Probe: what client APIs are reachable from the injected Lua?
CS.UnityEngine.Debug.Log("[dsh] lua-alive")

local function log(msg)
    CS.UnityEngine.Debug.Log("[dsh] " .. tostring(msg))
end

local ok, err = pcall(function()
    log("UIManager=" .. tostring(CS.RPG.Client.UIManager))
    local ui = CS.RPG.Client.UIManager
    if ui == nil then
        return
    end
    local inst = ui.Instance
    log("instance=" .. tostring(inst))
    if inst == nil then
        return
    end
    local t = inst:GetType()
    log("type=" .. tostring(t.FullName))
    local ms = t:GetMethods()
    log("methodCount=" .. tostring(ms.Length))
    local names = {}
    for i = 0, ms.Length - 1 do
        if i < 60 then
            names[#names + 1] = ms[i].Name
        end
    end
    log("methods=" .. table.concat(names, ","))
end)
if not ok then
    log("probe-error=" .. tostring(err))
end
