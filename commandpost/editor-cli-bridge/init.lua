--- Editor CLI bridge for CommandPost 2.1.
--- Binds only to loopback and exposes a fixed subset of Final Cut actions.

local httpserver = require "hs.httpserver"
local fs = require "hs.fs"
local json = require "hs.json"
local timer = require "hs.timer"
local just = require "cp.just"
local fcp = require "cp.apple.finalcutpro"
local SaveSheet = require "cp.apple.finalcutpro.export.SaveSheet"

local mod = {}
local server = nil

local allowedHandlers = {
    editor_cli = true,
    global_menuactions = true,
    global_handler = true,
    fcpx_videoEffect = true,
    fcpx_audioEffect = true,
    fcpx_generator = true,
    fcpx_title = true,
    fcpx_transition = true,
}

local allowedControllerActions = {
    active_project = true,
    export_xml = true,
    duplicate_project = true,
    open_project = true,
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

local function allowedOutputPath(path)
    if type(path) ~= "string" or path:sub(1, 1) ~= "/" then return nil end
    local absolute = fs.pathToAbsolute(path)
    if not absolute or absolute ~= path then return nil end
    local home = os.getenv("HOME")
    local roots = {
        home .. "/Movies/Editor CLI Sessions/",
        home .. "/projects/SRC/editor-cli/canary-output/",
    }
    for _, root in ipairs(roots) do
        if path:sub(1, #root) == root then return path end
    end
    return nil
end

local function durationSeconds(value)
    if type(value) ~= "string" then return nil end
    local selected = nil
    for timecode in value:gmatch("%d+:%d+:%d+[:;]%d+") do selected = timecode end
    if not selected then return nil end
    local hours, minutes, seconds, frames = selected:match(
        "(%d+):(%d+):(%d+)[:;](%d+)"
    )
    local rate = fcp.viewer:framerate() or 25
    return tonumber(hours) * 3600 + tonumber(minutes) * 60
        + tonumber(seconds) + tonumber(frames) / rate
end

local function controllerAction(actionId, parameters)
    if not allowedControllerActions[actionId] then
        error("controller action is outside the allowlist")
    end
    parameters = parameters or {}
    fcp:launch()

    if actionId == "active_project" then
        local title = fcp.timeline.title:value()
        local duration = fcp.timeline.toolbar.duration:value()
        if not title or title == "" then error("no Final Cut project is open") end
        local seconds = durationSeconds(duration)
        if not seconds then error("Final Cut project duration is unavailable") end
        return {
            project = title,
            duration = duration,
            durationSeconds = seconds,
            libraryPaths = fcp:activeLibraryPaths(),
        }
    end

    if actionId == "export_xml" then
        local destination = allowedOutputPath(parameters.destination)
        if not destination then error("export destination is outside the allowlist") end
        if destination:sub(-7):lower() ~= ".fcpxml" then
            error("export destination must end in .fcpxml")
        end
        if fs.attributes(destination) then error("export destination already exists") end
        local directory, filename = destination:match("^(.*)/([^/]+)$")
        if not directory or fs.attributes(directory, "mode") ~= "directory" then
            error("export directory does not exist")
        end
        if not fcp:selectMenu({"File", "Export XML…"})
            and not fcp:selectMenu({"File", "Export XML..."}) then
            error("Final Cut Export XML menu action failed")
        end
        local sheet = SaveSheet(fcp.primaryWindow)
        if not just.doUntil(function() return sheet:isShowing() end, 10) then
            error("Final Cut XML save sheet did not appear")
        end
        sheet:setPath(directory)
        sheet:filename(filename)
        sheet:save()
        if not just.doUntil(function() return fs.attributes(destination) ~= nil end, 30) then
            error("Final Cut did not write the exported XML")
        end
        return {path = destination}
    end

    if actionId == "duplicate_project" then
        local before = fcp.timeline.title:value()
        if not fcp:selectMenu({"Edit", "Duplicate Project as Snapshot"})
            and not fcp:selectMenu({"Edit", "Duplicate Project As Snapshot"})
            and not fcp:selectMenu({"Edit", "Duplicate Project"}) then
            error("Final Cut duplicate-project action failed")
        end
        return {source = before, requestedName = parameters.name, preserved = true}
    end

    if actionId == "open_project" then
        local name = parameters.name
        if type(name) ~= "string" or name == "" then error("missing project name") end
        local opened = fcp.timeline:doOpenProject(name):Now()
        if not opened then error("Final Cut project could not be opened") end
        return {project = name}
    end
end

local function execute(actionManager, payload)
    local handlerId = payload.handler
    local actionId = payload.actionId
    if not allowedHandlers[handlerId] then error("handler is outside the allowlist") end
    if type(actionId) ~= "string" or actionId == "" then error("missing actionId") end

    if handlerId == "editor_cli" then
        return controllerAction(actionId, payload.parameters)
    end

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
