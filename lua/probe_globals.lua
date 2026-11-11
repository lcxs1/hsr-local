-- exec=tt
-- Probe 2: what globals are visible in the injected Lua state?
local function log(msg)
    CS.UnityEngine.Debug.Log("[dsh] " .. tostring(msg))
end

local ok, err = pcall(function()
    local globals = {}
    local count = 0
    for k, _ in pairs(_G) do
        count = count + 1
        local name = tostring(k)
        if string.find(name, "Module") or string.find(name, "Manager") or string.find(name, "Map") then
            globals[#globals + 1] = name
        end
    end
    log("globals=" .. count)
    table.sort(globals)
    log("matched=" .. table.concat(globals, ","))
    log("ModuleManager=" .. tostring(_G.ModuleManager))
    log("UIManager=" .. tostring(_G.UIManager))
    log("MapModule=" .. tostring(_G.MapModule))
    log("PlayerModule=" .. tostring(_G.PlayerModule))
end)
if not ok then
    log("probe2-error=" .. tostring(err))
end
