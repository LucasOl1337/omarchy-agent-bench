---
name: human-agent-coexistence
description: Computer use on Omarchy/Hyprland. Load for agent-bench, hyprctl input, grim/slurp of the human session, ydotool/wtype/xdotool, CUA, focus, clipboard, or workspaces 6–11. Includes builtin browsers, Playwright and CDP: every agent navigation belongs on a bench.
---

# Human / agent coexistence

Visual computer-use (mouse, keyboard, focus, clipboard, screen, vision) belongs on **agent-bench** nested desktops, Hyprland workspaces **6, 7, 8, 9, 10 and 11**. Choose the route by the input mechanism, not by “this task has a GUI”.

## Global rule

Every agent navigation and visual operation happens on a bench. That includes builtin browsers, Playwright and CDP. Independence from the global mouse does **not** authorize the human Chromium: new tabs there still interrupt the operator.

Read the installed `USAGE.md` (bench home, or `docs/coexistence.md` in this repo) and confirm the bench association before the first navigation. An authorized personal account is prepared and verified **on the bench**. File and API work stays in the current harness.

## Benches (workspaces 6–11)

```sh
agent-bench ensure NAME
agent-bench cdp NAME
agent-bench browser NAME http://localhost:3000
agent-bench screenshot NAME /absolute/path.png
```

Names: lowercase, digits, hyphen, max 40, unique per concurrent GUI task. Prefer MCP `agent-bench-mcp` (`bench_ensure`, `bench_cdp`, CUA with `bench=`). `agent-bench mcp NAME` is CUA for one name only. Do not attach harness computer-use to the human compositor.

Forms: CDP/Playwright on `bench_cdp` / `agent-bench cdp NAME`. Pixel CUA only when the page is opaque. Stable names plus `agent-bench keep` so logins survive (skill `named-login-bench`). When finished, `agent-bench stop NAME` except `padrao`. `agent-bench gc` cleans expired sessions; `.keep` protects authenticated profiles.

If a route fails, recover another route **inside the same bench**. Direct control of the human session only when the human asked for that one intervention. Saving/restoring the clipboard is not isolation.

Do not `hyprctl dispatch workspace`: it switches the workspace of the monitor under the human mouse. The supervisor places the viewer without following the operator. During human-control, wait; `resume` only if asked. Stop only your bench.

Workspaces 1–5 stay human. 6–11 are for agents. Do not allocate 12+: Hyprland opens that workspace on the focused monitor.

## Tab and bench hygiene

Close your own tabs that have no next action. Reuse the search tab and the tab already on that URL. Keep in-progress forms, unsaved drafts, and uncertain submissions; record why in the checkpoint. Save proof before closing; never resubmit to rebuild evidence. Do not close another agent’s tabs or the human’s. When releasing a bench, stop only what you own and say what must continue.
