# Security

agent-bench isolates **display, input, clipboard, D-Bus and the Chromium profile**. It does not confine the filesystem. A process started with `agent-bench exec` runs as your user and can read `$HOME`.

Treat a harness with this MCP the way you treat a terminal on the same account.

## Network

Xvnc listens on a Unix socket mode `0600` only (`-nolisten tcp`). Chromium remote debugging binds to `127.0.0.1` with an ephemeral port. Do not publish that port. Do not add `--remote-debugging-port` to the *human* Chromium.

## Human session

Agents must not:

- screenshot or attach CUA to the human compositor
- write the user ydotool socket
- enable remote debugging on the personal browser

The control lock only covers commands that go through `agent-bench` and `agent-bench-mcp`. Older clients already attached to the nested display are not retroactively paused.

## Reports

Please open a GitHub issue for flaws in the isolation *story* (viewer landing on workspace 1–5, CDP falling back to the human browser, clipboard leak). Do not attach cookies or session logs.
