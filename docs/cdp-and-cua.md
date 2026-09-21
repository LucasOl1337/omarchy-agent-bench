# CDP first, CUA second

Two ways to drive a page, both **inside** a named bench.

## CDP / Playwright (preferred for forms)

`agent-bench cdp NAME` (or MCP `bench_cdp`) starts the bench, opens the bench Chromium if needed, and prints:

`agent-bench browser-status NAME` performs a read-only location check. It resolves the profile lock, verifies that the PID belongs to `agent-bench@NAME.service`, and only then inspects the endpoint. Browser startup requires an existing `chromium/Default` under that bench; it never seeds from another profile or creates an empty fallback.

```json
{
  "status": "conectado",
  "port": 12345,
  "webSocketDebuggerUrl": "ws://127.0.0.1:12345/devtools/browser/…",
  "playwright": "chromium.connectOverCDP('http://127.0.0.1:PORT')"
}
```

Use that URL only. Chromium is launched with `--password-store=basic`, `--user-data-dir` under the bench session, `--remote-debugging-port=0` (ephemeral, localhost), and X11 ozone. After Xvnc starts, `dbus-update-activation-environment` publishes `DISPLAY` on the bench D-Bus so portals and keyring dialogs stay on that screen.

Do not:

- connect to a debugging port on the human Chromium
- start `chromium` inside the bench without `agent-bench browser` / `bench_browser` (it may attach to a process outside)
- share one CDP client across two bench names

## Pixel CUA

`agent-bench-mcp` injects a `bench` argument into CUA tools (`left_click`, `type`, `screenshot`, …). First call `bench_ensure`. The hub starts the nested display (~0.3 s if cold) and routes input through the same lock as the CLI.

Use CUA when the page is a canvas, a game, or otherwise opaque to the accessibility/CDP tree. For HTML forms, CDP is faster and does not miss hit-targets.

`agent-bench mcp NAME` is a dedicated CUA server for a single name. The multiplexor is the default for Cursor, Claude, Codex, Hermes, Gemini, and OpenCode.

## Owner

`AGENT_BENCH_OWNER` (or an inferred harness id: `cursor`, `claude`, `codex`, …) is written to `owner.json` on `ensure`. `agent-bench list` shows it. Stop only benches you own.

## Control lock

While `human-control` exists on the runtime dir, mutating MCP tools and `exec` fail with a clear error. Wait. Do not `resume` yourself.
