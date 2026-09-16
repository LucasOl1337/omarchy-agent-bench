# Changelog

## Unreleased

- Visit-and-leave: Super+6 stays view-only; `agent-bench collaborate --here` / Super+Alt+A takes the focused viewer; leaving the reserved workspace (Super+1) auto-resumes the agent.

## 0.1.0 — 2026-09-16

First public snapshot of the nested X11 benches used on Omarchy (Hyprland):

- `agent-bench` CLI (`ensure`, `cdp`, `keep`, `gc`, control lock)
- multiplexed MCP `agent-bench-mcp` (`bench_ensure`, `bench_cdp`, CUA with `bench=`)
- Hyprland rules for workspaces **6–11** without pinning monitor serials
- systemd user units with memory/task limits and an idle reaper
- Agent Skills: coexistence, operations, named persistent login
- English + Portuguese README and community docs

The live tree on the original machine may still live under `~/.agents`; this repository is the portable install (`~/.local/share/omarchy-agent-bench`).
