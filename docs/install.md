# Install

## Dependencies

On Omarchy most of this is already present. On a stock Arch Hyprland user session, install at least:

- `tigervnc` (`Xvnc`, `vncviewer`)
- `openbox`
- `chromium`
- `xdotool` `xclip` `xterm` `xorg-xsetroot` `xorg-xauth`
- `python` (3.12+)
- `dbus` (user session)
- optional: `playwright` (or any CDP client), `cua-driver` for pixel CUA

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
| `~/.config/systemd/user/agent-bench@.service` | One unit per bench name |
| `~/.config/systemd/user/agent-bench-views.service` | Viewer supervisor |
| `~/.config/hypr/agent-bench.lua` | Window and persistent-workspace rules |
| `~/.local/share/applications/agent-bench.desktop` | “Agent benches” / “Bancada dos agentes” panel |

Options: `--prefix DIR`, `--no-enable`, `--no-skills`, `--no-hypr`.

The Hyprland snippet uses **persistent workspaces 6–11 without pinning a monitor serial**. Hyprland places them like any other persistent workspace. If you previously bound those numbers to specific monitors, compare the `.bak.*` file the installer writes next to `hyprland.lua` / `agent-bench.lua`.

Optional: take mouse/keyboard on the focused viewer with Super+Alt+A. Add this to `~/.config/hypr/bindings.lua`:

```lua
o.bind("SUPER + ALT + A", "Take focused bench",
  "bash -lc 'agent-bench collaborate --here || notify-send -u critical Bench \"Could not take the focused window\"'")
```

Super+6 stays view-only. Super+1 on that monitor gives the bench back.

`install.sh` does not stop or restart benches that are already running.

## MCP in the harness

Copy the snippet from [`contrib/mcp/`](../contrib/mcp/) into Cursor, Claude Desktop, or Codex config so `agent-bench-mcp` is on stdio. After that, tools include `bench_ensure`, `bench_cdp`, `bench_doctor`, and every CUA method with a `bench` argument.

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
