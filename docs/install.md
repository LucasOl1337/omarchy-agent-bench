# Install

## Dependencies

On Omarchy most of this is already present. On a stock Arch Hyprland user session, install at least:

- `tigervnc` (`Xvnc`, `vncviewer`)
- `openbox`
- `chromium`
- `xdotool` `xclip` `xterm` `xorg-xsetroot` `xorg-xauth`
- `python` (3.12+)
- `tk` for the Python Tkinter home window and human-opened panel
- `imagemagick` (`import`) for CLI screenshots
- `python-websocket-client` for `agent-bench-web` / the `bench_web` MCP tool
- `dbus` (user session)
- `cua-driver` supporting `dump-docs --type mcp` for the full/native MCP catalog and native CUA
- `libseccomp` (`libseccomp.so.2`) for the required native CUA process guard
- optional: `playwright` (or another CDP client)

The desktop and browser CLIs can run without CUA. A hub selection containing
only its own bench tools, such as `agent-bench-mcp --tools bench_list bench_web`,
also skips the CUA catalog and does not need `cua-driver`; selected operations
keep their normal dependencies. The default full catalog and selections with
native CUA tools require the static dump. A missing binary yields an explicit
`catalog_unavailable` error instead of a partial catalog. See [tool discovery](tool-discovery.md#lista-explícita-por-tarefa).
Importing the modules and reading CLI help do not start a browser or desktop.

Native CUA launch currently supports **Linux x86_64 with a 64-bit Python**.
Before executing a driver, its wrapper requires libseccomp, loads a filter that
denies `UI_DEV_CREATE`, and verifies the denial with an invalid file descriptor.
An unsupported ABI, missing dependency or failed check prevents driver execution;
there is no unprotected fallback. This guard neither enables pixel input nor
proves other CUA input paths are confined. See [CUA lifetime](cua-lifecycle.md#kernel-input-creation-guard)
for the exact scope and compatibility limits. No input device is opened by this
check; the installer itself does not start the driver or load a filter.

The CLI talks to `systemctl --user`. Linger is not required if you install from inside a graphical login.

## Install for this user

```sh
git clone https://github.com/LucasOl1337/omarchy-agent-bench.git
cd omarchy-agent-bench
./install.sh
```

Defaults:

| Path | Purpose |
| --- | --- |
| `~/.local/share/omarchy-agent-bench` | Code, Openbox config, session profiles |
| `~/.local/bin/agent-bench` | CLI symlink |
| `~/.local/bin/agent-bench-mcp` | Multiplexed MCP |
| `~/.local/bin/agent-bench-web` | Semantic browser CLI, same implementation as MCP |
| `~/.local/bin/agent-bench-profile` | Initial offline copy from the official agent seed |
| `~/.config/systemd/user/agent-bench@.service` | One unit per bench name |
| `~/.config/systemd/user/agent-bench-views.service` | Viewer supervisor |
| `~/.config/hypr/agent-bench.lua` | Window and persistent-workspace rules |
| `~/.local/share/applications/agent-bench.desktop` | “Agent benches” / “Bancada dos agentes” panel |

Options: `--prefix DIR`, `--no-enable`, `--no-skills`, `--no-hypr`.

Relative prefixes are resolved to absolute paths before generating symlinks and
templates. Symlinked prefix components or a symlinked `desktop` directory are
refused rather than traversed.

`--no-enable` copies the files without contacting systemd or reloading Hyprland:
no daemon reload, enable or start. To also leave Hyprland configuration files
untouched, use `--no-hypr`.
The installer still writes its CLI links, application entry and unit files; it
is not a read-only check or a live-safe updater. After reviewing an offline
installation, reload the user manager and enable the desired units explicitly.

The Hyprland snippet uses **persistent workspaces 6–11 without pinning a monitor serial**. Hyprland places them like any other persistent workspace. If you previously bound those numbers to specific monitors, compare the `.bak.*` file the installer writes next to `hyprland.lua` / `agent-bench.lua`.

Optional: take mouse/keyboard on the focused viewer with Super+Alt+A. Add this to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + A", "Take focused bench",
  "bash -lc 'agent-bench collaborate --here || notify-send -u critical Bench \"Could not take the focused window\"'")
```

This package installs workspace rules, not visit/take-control key bindings.
An ordinary workspace switch leaves agent input active; the human can use
`agent-bench visit NAME` to enter and take control. Leaving the bench workspace
on all monitors returns control. See [coexistence](coexistence.md).

`install.sh` does not stop or restart benches that are already running.
Reinstallation preserves `desktop/sessions` and `desktop/browser-seed` as-is,
including existing links or unknown state. It does not traverse, refresh,
repair or substitute template text in those entries, and neither directory is
copied from the source package. Code, user units and global configuration may
still be replaced: do not run this installer over a live managed installation
without the operator's explicit authorization.

## MCP in the harness

Copy the snippet from [`contrib/mcp/`](../contrib/mcp/) into Cursor, Claude Desktop,
or Codex config so `agent-bench-mcp` is on stdio. The harness must have
`~/.local/bin` on PATH; otherwise use the absolute installed executable path in
its command field. Do not assume the harness expands `~` or `$HOME` in that field.
Tools include `bench_ensure`, `bench_cdp`, `bench_doctor`, `bench_web`, and supported
CUA methods with a `bench` argument. Methods without a verified bench route are
filtered out; see [native isolation](native-isolation.md).

`agent-bench mcp NAME` is the older single-bench CUA server. Prefer the multiplexor so one connection can talk to several names.

## Skills

`./install.sh` copies [`skills/`](../skills/) into `~/.agents/skills` when that directory exists. Otherwise leave them in the prefix and symlink:

```sh
mkdir -p ~/.agents/skills
ln -s ~/.local/share/omarchy-agent-bench/skills/human-agent-coexistence ~/.agents/skills/
ln -s ~/.local/share/omarchy-agent-bench/skills/agent-bench ~/.agents/skills/
ln -s ~/.local/share/omarchy-agent-bench/skills/named-login-bench ~/.agents/skills/
```

Point Cursor rules / `AGENTS.md` / `CLAUDE.md` at the coexistence skill so *every* session loads the 6–11 rule.

## First bench

```sh
agent-bench ensure demo
agent-bench doctor demo
journalctl --user -u agent-bench@demo.service -u agent-bench-views.service -e
```

`padrao` is enabled at login as a spare empty desktop. You do not have to use it. Product logins should use a **stable name** and `agent-bench keep` — see [named benches](named-benches.md).

The installer does not bundle an authenticated browser seed or create an empty
profile as fallback. Before `agent-bench browser NAME` or `agent-bench cdp NAME`,
prepare the official offline agent seed and run `agent-bench-profile prepare NAME`
for a dry-run, then `--apply` for the initial copy. Existing bench profiles are
preserved. See [profile provisioning](profile-provisioning.md) for the source,
checks and authentication limits.

## Uninstall

```sh
./uninstall.sh
```

Profiles stay under the prefix until you pass `--purge-sessions`. Stop named benches yourself (`agent-bench stop NAME`) if you want the Xvnc processes gone.

## Tests (no display required)

```sh
cd desktop
python3 -m unittest discover -s . -p 'test_*.py'
```

Allocation, CDP URL parsing, `.keep` / GC, and viewer crash-vs-close recovery are unit-tested with mocks. They must not move the developer pointer.

`test_bench_install.py` runs the installer only against temporary source/prefix
trees, with `Path.home()` mocked and executable fixtures replacing service and
display commands. It checks reinstallation state, relative prefixes, copy-only
mode and the installed CLI/MCP entry points without a desktop or real systemd.
