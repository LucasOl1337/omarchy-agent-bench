-- Nested X11 benches for AI agents. Viewers land on workspaces 6–11
-- without stealing focus. Hyprland assigns those workspaces to a monitor
-- the same way it assigns any other persistent workspace: no serial pins.
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
      if name and ws and ws >= 6 and ws <= 11 then
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
for ws = 6, 11 do
  table.insert(agent_bench_workspace_rules, hl.workspace_rule({
    workspace = tostring(ws), persistent = true,
  }))
end
