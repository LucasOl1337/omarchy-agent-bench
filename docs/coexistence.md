# Human / agent coexistence

Load the skill [`skills/human-agent-coexistence/SKILL.md`](../skills/human-agent-coexistence/SKILL.md) in every harness that can see this machine.

## Map

| Workspaces | Owner | Allowed |
| --- | --- | --- |
| **1–5** | Human | Daily work. `hyprctl -j` read-only. No grim/slurp of those outputs, no ydotool/wtype/xdotool/XTEST, no CUA, no `hyprctl dispatch workspace`, no warping the pointer |
| **6–11** | Agents | TigerVNC viewers. Click, type, drag, capture, clipboard *inside* the bench |
| **12+** | Nobody | Hyprland creates that workspace on the focused monitor (where the human mouse is) |

The supervisor (`agent-bench-views.service`) only assigns 6–11. If all six have viewers, it stacks. It never allocates 12.

## Taking control

The panel **Agent benches** (launcher) lists live names. Super+6… visits without pausing the agent (view-only). Bind **Super+Alt+A** to `agent-bench collaborate --here` if you want a keyboard shortcut.

1. **Show screen** — reopen the viewer on its reserved workspace without changing *your* active workspace.
2. **Take control** / Super+Alt+A — waits until the current agent command finishes, then enables keyboard/mouse on the viewer and pauses agent input.
3. **Give back to agent** — also happens automatically when the reserved workspace is no longer visible on any monitor (Super+1 on the monitor that was showing it). Disables viewer input, allows MCP/`exec` again, docks the window.

Agents must not call `resume` unless the human asked. `exec`, `launch`, `browser`, clipboard write, and MCP already apply the lock and dock the viewer.

Clipboard and PRIMARY stay unbridged in both modes.

## What “use my account / Chromium” means

It identifies **which commercial identity** to log into **on the bench**. It does not authorize opening tabs on the personal window in workspaces 1–5. Create or reuse the bench profile, then confirm the account *there*.

Do not add `--remote-debugging-port` to the human Chromium flags file. Each bench Chromium already has its own CDP bound to localhost.

## Hygiene

- Close your own finished tabs. Reuse the search tab and the tab already on that URL.
- Keep in-progress forms, unsaved drafts, and uncertain submissions. Record why in the checkpoint. Save proof *before* closing; never resubmit to rebuild evidence.
- Do not close another agent’s tabs or the human’s.
- When releasing a bench, stop only what you own. Leave `padrao` alone unless asked.

## Recovery without touching the human session

If viewers vanish but `agent-bench list` still shows a display:

1. `journalctl --user -u agent-bench-views.service`
2. `systemctl --user start agent-bench-views.service` — restarts viewers, not the nested desktops
3. `agent-bench dock NAME` or `bench_ensure`

A crashed viewer (`X I/O error`, non-zero exit) is reopened after a few seconds. A normal close stays closed until you or the agent ask for the window again.

Full operational text also lives in [`desktop/USAGE.md`](../desktop/USAGE.md) (shown inside the bench home screen).
