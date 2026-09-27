# Changelog

## Unreleased

## 0.3.0 — 2026-09-27

- Login and Cloudflare: Chromium runs on a fixed CDP port per bench with the real GPU (no `--remote-debugging-port=0`, no `--disable-gpu`), so managed challenges pass with a CDP client attached. `DevToolsActivePort` is written by agent-bench.
- Profiles: a new bench gets an empty `.efemero` profile deleted on stop; only `cofre` is created persistent, and existing profiles are used as they are. `profile prepare` no longer clones the seed. Parallel `ensure` calls share one Chromium through an exclusive launch lock.
- `agent-bench-mcp` publishes the 20 measured core tools by default (~8.4k tokens instead of ~20k); `--tools all` keeps the full catalog. A missing cua-driver drops only the native tools.
- Reaper: stops a bench after 25 min without agent commands, CDP clients or native CPU work; leftover tabs and idle Electron apps no longer pin it.
- `agent-bench fila`: one heavy slot (3D/WebGL, video, long capture) at a time, FIFO, 20 min TTL. Benches outside the slot are capped at 1.5 cores and all benches at 8 (systemd drop-ins shipped in `contrib/systemd`).
- Bench server records which agent, project and launcher drive each bench in `runtime/<name>/origins.json`.
- New tools: `agent-bench-web` (accessibility snapshots, element refs, tab ownership), `agent-bench-profile` (offline seed provisioning) and `agent-bench-native` (opt-in conventional Chromium with native control).
- Native control refuses cua-driver 0.29 `install_extension`, next to `set_config` and `launch_app`, because it writes into the shared HOME.
- ExecStop closes Chromium before Xvnc so cookies flush; the views supervisor publishes `bar.json` for the Omarchy bar; `visit` lands on a shared workspace.
- `install.sh` with prefix `~/.agents` touches only its own entries instead of replacing the shared skills hub and `bin/`.

## 0.2.0 — 2026-09-21

- Visit-and-leave: Super+6 stays view-only; `agent-bench collaborate --here` / Super+Alt+A takes the focused viewer; leaving the reserved workspace (Super+1) auto-resumes the agent.
- `browser-status` proves the Chromium PID belongs to the requested bench before exposing its CDP endpoint.
- The MCP hub and CLI report workspace, control mode, browser location, task pressure, and actionable recovery errors.
- Browser startup requires an already prepared `Default` profile in the named bench. It never copies cookies or creates an empty identity as fallback.
- Human visit/close decisions are covered by unit tests, including workspaces 6–11 and viewer ownership.

## 0.1.0 — 2026-09-16

First public snapshot of the nested X11 benches used on Omarchy (Hyprland):

- `agent-bench` CLI (`ensure`, `cdp`, `keep`, `gc`, control lock)
- multiplexed MCP `agent-bench-mcp` (`bench_ensure`, `bench_cdp`, CUA with `bench=`)
- Hyprland rules for workspaces **6–11** without pinning monitor serials
- systemd user units with memory/task limits and an idle reaper
- Agent Skills: coexistence, operations, named persistent login
- English + Portuguese README and community docs

The live tree on the original machine may still live under `~/.agents`; this repository is the portable install (`~/.local/share/omarchy-agent-bench`).
