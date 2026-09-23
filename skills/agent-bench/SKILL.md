---
name: agent-bench
description: Operate nested X11 agent benches on Omarchy/Hyprland. Load for agent-bench CLI, agent-bench-mcp, benches on workspaces 6–11, CDP, CUA with bench=, ensure/keep/gc, or when tempted to use the human Chromium.
---

# agent-bench

Nested Xvnc benches. Empty desktop ~0.3 s; Chromium is the cost. Do not create an extra desktop. Do not use `padrao` for product logins. Never drive workspaces 1–5.

## Commands

```sh
agent-bench ensure NAME          # start if needed, dock viewer
agent-bench cdp cofre            # the only logged-in browser (never copied)
agent-bench-profile prepare NAME --apply # empty throwaway profile; the old seed is no longer copied
agent-bench browser-status NAME  # require this bench's own profile/process
agent-bench cdp NAME            # CDP JSON + Playwright connectOverCDP
agent-bench browser NAME [URL]
agent-bench screenshot NAME /abs.png
agent-bench exec NAME -- xdotool mousemove 400 300 click 1
agent-bench launch NAME -- APP ARG... # native app tied to the bench, not the MCP connection
agent-bench keep NAME 'why login must survive'
agent-bench list
agent-bench doctor NAME
agent-bench stop NAME           # not padrao; not mid-form
agent-bench gc                  # dry-run; gc --apply deletes expired dirs
```

MCP stdio: `agent-bench-mcp`. Tools: `bench_ensure`, `bench_cdp`, `bench_doctor`, `bench_gc`, plus CUA with `bench`. Set `AGENT_BENCH_OWNER` if the harness id would be wrong.
The hub's `tools/list` reads the static filtered catalog without `ensure` or
starting a desktop/driver. Discovery is not proof that a bench is ready.

The profile preparer copies only the offline official agent seed into a new
bench profile. Existing destinations are preserved; it does not repair, refresh,
create an empty identity or prove login. Confirm the authorized account after
starting the browser in the bench. See [profile preparation](../../docs/profile-provisioning.md).

Semantic browser control: `bench_web` through that MCP, or
`agent-bench-web --bench NAME --mission TASK open --url https://example.org`.
Use the returned tab with `observe --tab ID`, then a current ref with `click`
or `fill`. Input/navigation and new observations invalidate element refs;
`read --snapshot ID` paginates, filters or reads regions without replacing refs.
`observe --since ID` reports changes; a reset requires reading the returned state.
For local iframe content, discover `frames --tab ID`, then use an available
`--frame FRAME_REF` with `observe`, `read`, `fill` or `click`. OOPIF is unavailable;
use returned refs and rediscover after document changes, never a guessed frameId.

Only the mission's own tabs or explicitly adopted children can be operated.
Use `popups --tab OPENER` then `adopt --tab OPENER --popup REF` for a verified
direct child, followed by observation. URLs are not ownership proof. CLI and MCP
share the registry. The browser must already be prepared and running here.

`upload --tab ID --ref FILE_INPUT_REF --file /absolute/file` selects an authorized
file in the observed input; repeat `--file` only for a multiple-file input.
Selection can transmit immediately. `selection_verified` compares names/sizes;
`transfer_verified: false` does not imply no transmission. Reconcile site state
before retrying an uncertain selection. See [browser control](../../docs/semantic-browser.md).

A new MCP connection may select names with `agent-bench-mcp --tools bench_list`
or a task-specific list. Omitting the option keeps every available tool. Hub-only
selections need no CUA binary for discovery; out-of-list calls are refused before
dispatch. See [tool discovery](../../docs/tool-discovery.md). `bench_doctor` calls
`ensure`, so use `bench_list` for a purely read-only discovery check.
It reports responding benches, assigned workspace and actor metadata including
`owner`, `last_actor` and `last_seen_at`, without refreshing activity. Assignment
does not prove a live viewer; actor labels are not an exclusive lease.

Native CUA uses `list_windows` and `get_window_state` with observed PID/window and
current element tokens. Explicit PIDs are checked against the bench; legacy
`get_accessibility_tree`, `page`/`browser_*`/`get_browser_state` and trajectory
replay are refused. Use `bench_web` or validated bench CDP for browser work.
`set_config` is refused because it persists shared configuration. `get_config`
is read-only; `get_window_state.max_dimension` limits one image within the
existing ceiling. Use `bench_launch`/CLI `launch` for lasting native apps, not
`launch_app`, whose child can be stopped when its driver connection closes.

For pixels and keys, use `bench_exec`/CLI `exec` with xdotool on the bench's
exclusive DISPLAY; do not override it. CUA pixel input has a local proof with
the integrated private-environment gate and seccomp denial of `UI_DEV_CREATE`.
Use a new client through the current runtime. Both MCP routes default omitted
`delivery_mode` to `foreground` for click, double_click, right_click, drag,
type_text, press_key, hotkey and scroll; explicit values are preserved.
Click and typing with omitted mode were confirmed in the Chromium fixture.
Existing drivers do not gain these protections retroactively.
Foreground click, drag, ASCII and End worked in one Chromium fixture;
background returned `EPERM` without a new XI2 master. A legacy XI2 orphan also
affected xdotool typing; recovery followed verified orphan removal without a
restart. Check the actual field/focus and bench XI2 state on failure; do not
automatically clean devices or repeat mutations. This is not a general sandbox.
With the installed CUA 0.28.1, explicit-element `type_text` can use AT-SPI even
in foreground and truncate Unicode: `InsertText` gets characters instead of UTF-8
bytes. `set_value` first tries `SetTextContents`; its fallback inserts without
clearing and has the same length bug. The GTK `set_value` proof was exact via the
app's save callback despite an unverifiable effect and no tree value. It does not
prove every fallback replaces correctly. Never silently change insertion into
replacement, and do not assume keyboard input supports arbitrary Unicode.

Choose bench clipboard plus paste deliberately: write with `bench_clipboard_set`,
read with `bench_clipboard_get`, compare, then select the intended field/caret and
verify the pasted content. The hub reports `CLIPBOARD_FAILED` for process failure
or `CLIPBOARD_RESULT_INVALID` for malformed replies; successful set/get still
return `ok: true` / `text`. Neither proves the app received the paste. The sequence
is not atomic: reconcile partial/uncertain results before another action, without
automatic replay. Use only the bench clipboard; do not restart another bench.

## Always

- Confirm `doctor` / `status` shows a reserved workspace 6–11 before clicking.
- Require `browser-status` to report `lives_in == NAME` and that bench's own `user_data_dir`.
- Prefer `bench_web`/validated CDP for HTML; use bench X11 input for opaque controls.
- One exclusive name per concurrent GUI task; never share a user-data-dir between processes.
- Close finished owned tabs. Preserve pending forms, uncertain submissions and unsaved native work.
- Downloads use the observed UI and a private destination; verify the actual file/name before repeating anything. The local Save As proof is not a new download API or permission to change global CDP download settings.
- Idle cleanup retains native processes or an uncertain inventory; it does not save documents. `keep` protects disk GC only.

## Never

- Human Chromium, personal `--remote-debugging-port`, workspaces 1–5.
- `hyprctl dispatch workspace`, grim of human outputs, ydotool on the user socket, `resume` without being asked.
- `stop padrao`. `gc --apply` on a `.keep` login (GC already skips `.keep`; do not delete the folder by hand).
- Workspace 12+.

If MCP tools are missing, check the connection/schema or use the CLI; an old
client may need its owner to reconnect. Do not restart other harnesses.
Reconnect old clients to load the current guards and transport deadlines. The
dedicated `agent-bench mcp NAME` bounds initialize to 15 s and other driver
exchanges to 60 s, not the whole session. A transport failure ends that connection
and releases its input gate; `CUA_RESULTADO_INCERTO` requires inspecting the app
before deciding the next action. Explicit reconnect never replays the request.
See [CUA lifecycle](../../docs/cua-lifecycle.md) for the sequential transport limits.
`cua-driver status` without a global daemon is not a failure of this MCP.

Details: https://github.com/LucasOl1337/omarchy-agent-bench/blob/main/docs/cdp-and-cua.md and troubleshooting.md in the same folder.
