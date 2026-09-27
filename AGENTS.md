# Agent notes for this repository

Visual work for this project (screenshots of the UI, clicking the panel, verifying viewers) uses **agent-bench** on workspaces **6–11**. Do not drive Hyprland workspaces 1–5. Do not `hyprctl dispatch workspace`.

File edits, tests, and `git` stay in the harness.

```sh
cd desktop
python3 -m unittest discover -s . -p 'test_*.py'
```

Do not run `./install.sh` on a machine that already has a live `~/.agents` agent-bench tree unless the operator asked: the installer overwrites user systemd units and `~/.config/hypr/agent-bench.lua`.

Do not commit `desktop/sessions/` or other live profiles.

For conventional Chromium without CDP, or login failures attributed to debugging
mode, read [native browser operation](docs/browser-native.md). Use a named bench
and preserve existing browsers. Do not claim a login fix from launch flags alone.
