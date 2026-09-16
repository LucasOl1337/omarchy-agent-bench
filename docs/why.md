# Why nested benches exist

A coding agent on a personal Hyprland session has the same powers you do. That is the product. It is also how the desktop becomes unusable:

1. **Pointer and focus.** `hyprctl dispatch workspace 7` switches the workspace on the *focused monitor* — the one under your mouse. The agent meant to look at its own window and yanked yours.
2. **Human Chromium.** Playwright, CDP, “use my Chrome”, and harness-builtin browsers all prefer an already-running profile. New tabs land on the page you were reading.
3. **Clipboard and screenshots.** `grim` of the session, `xclip` of PRIMARY, `ydotool` on the user socket: the agent copies your password prompt or types into the focused terminal.
4. **Workspace overflow.** Hyprland creates workspace 12+ on the monitor that currently has the pointer. A fourth “agent desktop” that picks 12 appears on top of you.

Workspaces 1–5 staying human, and 6–11 being reserved for agent viewers, is the coexistence rule this repo implements. The nested display is ordinary Xvnc: 1600×1000, software rendering, Unix-socket VNC with mode 0600, no TCP port, clipboard bridge off until you take control.

You do not need a VM. Files, git, and APIs stay in the harness. Only *visual* work moves: mouse, keyboard, browser, screenshots, CUA.

## What we tried not to do

- **Do not attach computer-use to the human compositor.** Even “just screenshots” of the human session is surveillance of the operator.
- **Do not enable `--remote-debugging-port` on the personal Chromium.** Sites flag it; it also invites every agent to the same profile.
- **Do not pick workspace 12+.** The allocator in `bench_views.py` only chooses 6–11 and stacks if all six are busy.
- **Do not kill a bench to “refresh” it** while a form is open. `ensure` is cheap. `stop` is for when the owner is done.

## Related Omarchy tools

This repository is the nested desktop. Other public pieces from the same author:

- [omarchy-agents](https://github.com/LucasOl1337/omarchy-agents) — waybar panel of running agents
- [ponte](https://github.com/LucasOl1337/ponte) — phone remote for *your* session (not for agents)
- [sonora](https://github.com/LucasOl1337/sonora) — per-app volume on Omarchy

If you run several harnesses at once (Cursor, Claude, Codex, Hermes), give each concurrent GUI task a **distinct bench name**. Share a name only when you *want* the same Chromium profile (logged-in product, same cookies).
