# Architecture

Verified on Omarchy (Hyprland) in 2026. Agents keep using the same files and APIs. Visual work goes to a local Xvnc bench.

```mermaid
flowchart TB
  subgraph host [Human Hyprland session]
    W15[Workspaces 1-5]
    W611[Workspaces 6-11]
    Views[agent-bench-views.service]
    Panel[bench_panel.py]
    MCP[agent-bench-mcp]
    CLI[agent-bench]
  end
  subgraph bench [Per-name user service]
    Xvnc[Xvnc 1600x1000 unix socket]
    OB[Openbox]
    Home[welcome.py]
    Chr[Chromium + CDP]
    DBus[dbus-run-session]
  end
  CLI --> Unit[agent-bench@NAME.service]
  MCP --> CLI
  Unit --> DBus --> Xvnc
  Xvnc --> OB
  OB --> Home
  OB --> Chr
  Views --> Viewer[vncviewer]
  Viewer --> W611
  Panel --> Views
```

## Process boundaries

- `agent-bench@NAME.service` → `dbus-run-session` → `Xvnc` + Openbox + home UI. `KillMode=control-group` so `stop` takes children.
- Display numbers start at `:80` so they never collide with a typical `:0`/`:1`. Xauthority is per bench.
- VNC: `-rfbunixpath`, mode `0600`, `-nolisten tcp`, cut-text off, pointer/key off until human-control flips `AllowOverride`.
- After Xvnc is ready: `dbus-update-activation-environment DISPLAY XAUTHORITY …` so dialogs belong to the nested bus.
- Chromium: `--password-store=basic` (no host gnome-keyring prompt on the wrong screen).

## Viewer placement

`bench_views.py` holds a Unix RPC socket. On `ensure` / `dock` it picks a free id in `6..11`, writes `view-workspaces.tsv`, and `hyprctl eval`s `agent-bench.lua`. Window rules match class `Vncviewer` and title `Bancada dos agentes — NAME - TigerVNC`, `workspace N silent`, `no_initial_focus`.

Persistent workspace rules for 6–11 have **no `monitor =`**. Pinning Samsung/YSN serials is a local operator choice, not something this repo ships.

## MCP hub

`bench_hub_mcp.py` exposes bench tools plus proxied CUA. Mutating CUA calls take the same `control()` lock as the CLI (`input.lock` + refuse if `human-control` exists + `views('dock')`). `bench_gc` defaults to dry-run.

## Lifecycle

- **ensure**: start unit if needed (~0.3 s for empty Xvnc), claim owner, dock viewer, optional Chromium.
- **reaper** (inside views loop): stop units that are not `padrao`, not human-control, not mid-command, Chromium idle/blank, last activity older than `AGENT_BENCH_IDLE_SECONDS`.
- **gc**: delete session directories older than `AGENT_BENCH_GC_DAYS` unless `.keep` or the unit is active. Default is dry-run.

## Not a sandbox

The nested user is still you. `exec` can read `$HOME`. Treat untrusted code as you would in a terminal on the host.

GPU, exclusive Wayland clients, and games are out of scope.

## Layout after install

```
~/.local/share/omarchy-agent-bench/
  bin/agent-bench
  bin/agent-bench-mcp
  desktop/          # python modules, USAGE.md, openbox.xml
  desktop/sessions/ # per-name Chromium + logs
  skills/
contrib/            # sources for lua, systemd, desktop, MCP snippets
```

Runtime: `$XDG_RUNTIME_DIR/agent-bench/` (`views.sock`, per-name `control.sock`, `human-control`, `session.json`).
