--- Editor CLI bridge for CommandPost 2.1.
--- Binds only to loopback and exposes a fixed subset of Final Cut actions.

local httpserver = require "hs.httpserver"
local json = require "hs.json"
local timer = require "hs.timer"
local fcp = require "cp.apple.finalcutpro"

local mod = {}
local server = nil

local allowedHandlers = {
    global_menuactions = true,
    global_handler = true,
    fcpx_videoEffect = true,
    fcpx_audioEffect = true,
    fcpx_generator = true,
    fcpx_title = true,
    fcpx_transition = true,
}

local allowedGlobalActions = {
    fcpxPlayPause = true,
    fcpxUndo = true,
    fcpxRedo = true,
}

local allowedMenus = {
    ["File/Export XML…"] = true,
    ["File/Export XML..."] = true,
    ["File/Import/XML…"] = true,
    ["File/Import/XML..."] = true,
    ["Edit/Duplicate Project as Snapshot"] = true,
    ["Edit/Duplicate Project"] = true,
}

local function response(id, status, value)
    local result = {
        type = "response",
        id = id,
        status = status,
        timestamp = timer.secondsSinceEpoch(),
    }
    if status == "success" then
        result.result = value
    else
        result.error = tostring(value)
    end
    return json.encode(result)
end

local function splitPath(value)
    local path = {}
    for part in value:gmatch("[^/]+") do
        if part ~= "Final Cut Pro" then table.insert(path, part) end
    end
    return path
end

local function normalizedPath(path)
    local parts = {}
    for part in path:gmatch("[^/]+") do
        table.insert(parts, part:gsub("%.localized", ""))
    end
    return table.concat(parts, "/")
end

local function pluginChoice(handler, actionId)
    local choices = handler._choices or handler:choices()
    if not choices then return nil end
    for _, choice in ipairs(choices:getChoices()) do
        local params = choice.params
        if type(params) == "table" then
            local candidate = params.path and normalizedPath(params.path)
            local named = params.category and params.name
                and (params.category .. "/" .. params.name)
            if candidate and candidate:sub(-#actionId) == actionId then return params end
            if named == actionId then return params end
        end
    end
    return nil
end

local function execute(actionManager, payload)
    local handlerId = payload.handler
    local actionId = payload.actionId
    if not allowedHandlers[handlerId] then error("handler is outside the allowlist") end
    if type(actionId) ~= "string" or actionId == "" then error("missing actionId") end

    if handlerId == "global_menuactions" then
        local path = splitPath(actionId)
        local key = table.concat(path, "/")
        if not allowedMenus[key] then error("menu action is outside the allowlist") end
        fcp:launch()
        if not fcp:selectMenu(path) then error("Final Cut menu action failed") end
        return {selected = key}
    end

    local handler = actionManager.handlers()[handlerId]
    if not handler then error("handler is unavailable") end
    if handlerId == "global_handler" then
        if not allowedGlobalActions[actionId] then
            error("global action is outside the allowlist")
        end
        return handler:execute({id = actionId})
    end

    local choice = pluginChoice(handler, actionId)
    if not choice then error("Final Cut asset is unavailable") end
    return handler:execute(choice)
end

local function handle(actionManager, message)
    local ok, data = pcall(json.decode, message)
    if not ok or type(data) ~= "table" then
        return response(nil, "error", "invalid JSON")
    end
    if data.type == "ping" then
        return response(data.id, "success", {message = "pong"})
    end
    if data.type ~= "command" or type(data.payload) ~= "table" then
        return response(data.id, "error", "invalid command")
    end
    local executed, result = pcall(execute, actionManager, data.payload)
    return response(data.id, executed and "success" or "error", result)
end

local plugin = {
    id = "screddy.editor_cli.bridge",
    group = "screddy",
    required = false,
    dependencies = {
        ["core.action.manager"] = "actionManager",
    },
}

function plugin.init(deps)
    mod.start = function()
        if server then return true end
        server = httpserver.new(false, false)
        server:setInterface("loopback")
        server:setPort(27480)
        server:websocket("/", function(message)
            return handle(deps.actionManager, message)
        end)
        server:start()
        return true
    end
    mod.stop = function()
        if server then
            server:stop()
            server = nil
        end
    end
    return mod
end

function plugin.postInit()
    mod.start()
end

return plugin
