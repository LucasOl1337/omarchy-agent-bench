# Troubleshooting

## Viewer gone, desktop still alive

`agent-bench list` shows `display: :8N` and `viewer_pid: null`. Apps keep running.

```sh
journalctl --user -u agent-bench-views.service -e
systemctl --user start agent-bench-views.service
agent-bench dock NAME
```

Crash / signal exits reschedule a reopen. A clean viewer close stays closed.

## `Acompanhamento indisponível`

The views socket is down. Start `agent-bench-views.service`. New input commands refuse to run against the human session instead of “helpfully” clicking there.

## Human took control

Error: *O humano assumiu esta bancada.* Wait for **Give back to agent**. Do not `agent-bench resume` unless asked.

## CDP `fechado` or `endpoint sem conexão`

```sh
agent-bench browser NAME
agent-bench cdp NAME
```

Invalid `DevToolsActivePort` is treated as disconnected. The code does **not** fall back to a browser on `$DISPLAY` of the human session.

## Workspace 12 appeared on my monitor

A custom script allocated outside 6–11, or Hyprland created a workspace because something focused it. Stop that. This project’s allocator never picks 12+. Move the stray window away; do not `hyprctl dispatch workspace 12` from an agent.

## `hyprctl dispatch workspace` moved *me*

That command switches the workspace of the focused monitor. Agents must not use it. The supervisor places the viewer silently (`no_initial_focus`, suppress activate).

## Limits / OOM

Each bench is a systemd user service with `MemoryMax=6G`. Chromium is the usual consumer. `stop` the bench you own; do not kill another unit.

## Logs

```sh
journalctl --user -u agent-bench@NAME.service -e
# session log:
less ~/.local/share/omarchy-agent-bench/desktop/sessions/NAME/session.log
```

If you installed elsewhere, the sessions directory is `$PREFIX/desktop/sessions`.

## MCP has no tools

The harness is not launching `agent-bench-mcp`. `cua-driver status` without a global daemon is normal: this MCP does not use the human CUA daemon. Probe `bench_doctor` or CUA `get_screen_size` with `bench` set.

More cases: [`desktop/USAGE.md`](../desktop/USAGE.md).
