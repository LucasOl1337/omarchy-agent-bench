---
name: agent-bench
description: Operate nested X11 agent benches on Omarchy/Hyprland. Load for agent-bench CLI, agent-bench-mcp, benches on workspaces 6–11, CDP, CUA with bench=, ensure/keep/gc, or when tempted to use the human Chromium.
---

# agent-bench

Nested Xvnc benches. Empty desktop ~0.3 s; Chromium is the cost. Do not create an extra desktop. Do not use `padrao` for product logins. Never drive workspaces 1–5.

## Commands

```sh
agent-bench ensure NAME          # start if needed, dock viewer
agent-bench cdp NAME            # CDP JSON + Playwright connectOverCDP
agent-bench browser NAME [URL]
agent-bench screenshot NAME /abs.png
agent-bench exec NAME -- xdotool mousemove 400 300 click 1
agent-bench keep NAME 'why login must survive'
agent-bench list
agent-bench doctor NAME
agent-bench stop NAME           # not padrao; not mid-form
agent-bench gc                  # dry-run; gc --apply deletes expired dirs
```

MCP stdio: `agent-bench-mcp`. Tools: `bench_ensure`, `bench_cdp`, `bench_doctor`, `bench_gc`, plus CUA with `bench`. Set `AGENT_BENCH_OWNER` if the harness id would be wrong.

## Always

- Confirm `doctor` / `status` shows a reserved workspace 6–11 before clicking.
- Prefer CDP for HTML forms. CUA only if the page is opaque.
- One exclusive name per concurrent GUI task. Share a name only to share cookies.
- Close finished tabs you opened. Do not `stop` while a form or uncertain submit is open.

## Never

- Human Chromium, personal `--remote-debugging-port`, workspaces 1–5.
- `hyprctl dispatch workspace`, grim of human outputs, ydotool on the user socket, `resume` without being asked.
- `stop padrao`. `gc --apply` on a `.keep` login (GC already skips `.keep`; do not delete the folder by hand).
- Workspace 12+.

If MCP tools are missing, the harness is not running `agent-bench-mcp`. `cua-driver status` without a global daemon is not a failure of this MCP.

Details: https://github.com/LucasOl1337/omarchy-agent-bench/blob/main/docs/cdp-and-cua.md and troubleshooting.md in the same folder.
