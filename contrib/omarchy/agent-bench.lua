-- Nested X11 benches for AI agents. Viewers land on the agent workspaces
-- without stealing focus. Default 6–11, with Hyprland placing them like any
-- other persistent workspace. ~/.config/agent-bench/workspaces can move them,
-- e.g. "6-10 monitor=ASUSTek COMPUTER INC VG32VQ1B": slots 6–10 of that
-- monitor's block under Omarchy 4.5 per-monitor workspaces (desktop/bench_workspaces.py
-- is the same rule on the Python side).
local function read_file(path)
  local file = path and io.open(path, "r")
  if not file then return nil end
  local text = file:read("a")
  file:close()
  return text
end

local home = os.getenv("HOME") or ""
local config_home = os.getenv("XDG_CONFIG_HOME") or (home .. "/.config")
local state_home = os.getenv("XDG_STATE_HOME") or (home .. "/.local/state")

local first, last, monitor = 6, 11, nil
for line in (read_file(config_home .. "/agent-bench/workspaces") or ""):gmatch("[^\n]+") do
  if not line:match("^%s*#") and line:match("%S") then
    local a, b, rest = line:match("^%s*(%d+)%s*%-%s*(%d+)(.*)$")
    a, b = tonumber(a), tonumber(b)
    if a and b and a >= 1 and a <= b and b - a < 10 then
      local wanted = rest and rest:match("^%s+monitor=(.-)%s*$")
      local block = nil
      if wanted then
        for state in (read_file(state_home .. "/omarchy/monitor-workspaces") or ""):gmatch("[^\n]+") do
          local n, identity = state:match("^(%d+)\t[^\t]*\t(.+)$")
          if n and identity:sub(1, #wanted) == wanted then block = tonumber(n) break end
        end
      end
      if block and b <= 10 then a, b = block * 10 + a, block * 10 + b end
      first, last = a, b
      if block then monitor = "desc:" .. wanted end
    end
    break
  end
end

if agent_bench_rules then
  for _, rule in ipairs(agent_bench_rules) do pcall(function() rule:remove() end) end
end
agent_bench_rules = {}
local runtime = os.getenv("XDG_RUNTIME_DIR")
local file = runtime and io.open(runtime .. "/agent-bench/view-workspaces.tsv", "r")
if file then
    for line in file:lines() do
      local name, workspace = line:match("^([a-z0-9-]+)%s+(%d+)$")
      local ws = tonumber(workspace)
      if name and ws and ws >= first and ws <= last then
        table.insert(agent_bench_rules, hl.window_rule({
          name = "agent-bench-" .. name,
          match = { class = "^Vncviewer$", title = "^Bancada dos agentes — " .. name .. " - TigerVNC$" },
          workspace = workspace .. " silent",
          no_initial_focus = true,
          suppress_event = "activate activatefocus",
        }))
      end
    end
    file:close()
end

if agent_bench_workspace_rules then
  for _, rule in ipairs(agent_bench_workspace_rules) do pcall(function() rule:set_enabled(false) end) end
end
agent_bench_workspace_rules = {}
for ws = first, last do
  table.insert(agent_bench_workspace_rules, hl.workspace_rule({
    workspace = tostring(ws), persistent = true, monitor = monitor,
  }))
end
