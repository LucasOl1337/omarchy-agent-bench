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

The panel **Agent benches** (launcher) lists live names. The generic installer and
`contrib/omarchy/agent-bench.lua` install viewer/workspace rules, not keyboard
bindings. An ordinary workspace switch only shows the viewer; it does not pause
agent input or enable human input. Existing human control remains in effect.

1. **Show screen** — a human button action that reopens and focuses the viewer on its reserved workspace, without taking control. Agent-side `ensure` only places the viewer and does not follow the human.
2. **Take control** — a human button action that enables viewer keyboard/mouse and pauses agent input. If an agent command holds the input lock, it returns a busy error; retry after the command finishes. It does not queue a handoff.
3. **Give back to agent** — also happens automatically after the reserved workspace is no longer visible on any monitor (for example, Super+1 on the monitor that was showing it). Disables viewer input, allows MCP/`exec` again, docks the window.

For a single human action that both enters and takes control, run
`agent-bench visit NAME`. `agent-bench visit 6` through `11` selects the unique
bench on that workspace; when several benches share it, use the bench name. A
workspace with no bench can be visited but grants no bench control. `visit`
focuses the host workspace/viewer, so it is a **human command**, not an agent route.
The human can also bind `agent-bench collaborate --here` to a takeover shortcut.

**Mark configuration:** its existing `bindings.lua` maps **Super+6** … **Super+9**,
**Super+0** (10) and **Super+Ctrl+0** (11) to `visit`, which requests control as part
of entering. **Super+Alt+A** calls `collaborate --here`. These are local bindings,
not defaults installed by this package. Other installations must check their own
configuration before relying on a shortcut.

If enabling human input fails, the supervisor removes only the pause created by
that attempt, and only when the server explicitly confirms input remains disabled.
An older human pause, enabled input or uncertain status keeps the pause. The error
includes task usage when the bench reports at least 90% of a finite task limit;
otherwise it points to that bench's service log. This does not restart the bench
or grant the agent permission to call `resume`.

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
