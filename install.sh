#!/usr/bin/env bash
# Install agent-bench into the current user account.
# Does not start or stop running benches. Review the Hyprland backup before reload.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
prefix="${AGENT_BENCH_PREFIX:-$HOME/.local/share/omarchy-agent-bench}"
enable_services=1
install_skills=1
reload_hypr=1

usage() {
  cat <<'EOF'
Install nested X11 benches for AI agents on Omarchy / Hyprland.

Usage: ./install.sh [options]

  --prefix DIR     Install tree (default: ~/.local/share/omarchy-agent-bench)
  --no-enable      Copy files only; do not enable systemd units
  --no-skills      Skip copying Agent Skills into ~/.agents/skills
  --no-hypr        Skip copying and requiring hypr.agent-bench
  -h, --help       Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --no-enable) enable_services=0; shift ;;
    --no-skills) install_skills=0; shift ;;
    --no-hypr) reload_hypr=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

python3 - "$root" "$prefix" "$install_skills" "$reload_hypr" <<'PY'
from pathlib import Path
import shutil
import sys
import time

root, prefix, install_skills, reload_hypr = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == '1', sys.argv[4] == '1'
home = Path.home()

if prefix.resolve() == root.resolve():
    raise SystemExit('Refusing to install into the source tree. Pick another --prefix.')

prefix.mkdir(parents=True, exist_ok=True)
for name in ('bin', 'desktop', 'contrib', 'skills', 'docs'):
    src, dst = root / name, prefix / name
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'sessions'))

for path in prefix.rglob('*'):
    if not path.is_file():
        continue
    text = path.read_text(encoding='utf-8', errors='ignore')
    if '@PREFIX@' in text:
        path.write_text(text.replace('@PREFIX@', str(prefix)))

bindir = home / '.local/bin'
bindir.mkdir(parents=True, exist_ok=True)
for name in ('agent-bench', 'agent-bench-mcp'):
    target = bindir / name
    if target.is_symlink() or target.exists():
        target.unlink()
    target.symlink_to(prefix / 'bin' / name)

apps = home / '.local/share/applications'
apps.mkdir(parents=True, exist_ok=True)
shutil.copy2(prefix / 'contrib/applications/agent-bench.desktop', apps / 'agent-bench.desktop')

systemd = home / '.config/systemd/user'
systemd.mkdir(parents=True, exist_ok=True)
for unit in ('agent-bench@.service', 'agent-bench-views.service'):
    shutil.copy2(prefix / 'contrib/systemd' / unit, systemd / unit)

if reload_hypr:
    hypr = home / '.config/hypr'
    main = hypr / 'hyprland.lua'
    if main.exists():
        rule = hypr / 'agent-bench.lua'
        source = prefix / 'contrib/omarchy/agent-bench.lua'
        if rule.exists() and rule.read_bytes() != source.read_bytes():
            shutil.copy2(rule, rule.with_name(f'agent-bench.lua.bak.{time.time_ns()}'))
        shutil.copy2(source, rule)
        body = main.read_text()
        if 'require("hypr.agent-bench")' not in body:
            shutil.copy2(main, main.with_name(f'hyprland.lua.bak.{time.time_ns()}'))
            with main.open('a') as config:
                config.write('\nrequire("hypr.agent-bench")\n')
    else:
        print('No ~/.config/hypr/hyprland.lua — copy contrib/omarchy/agent-bench.lua yourself.', file=sys.stderr)

if install_skills:
    hub = home / '.agents/skills'
    if hub.is_dir():
        for name in ('human-agent-coexistence', 'agent-bench', 'named-login-bench'):
            dst = hub / name
            src = prefix / 'skills' / name
            if dst.exists() or dst.is_symlink():
                backup = hub / f'{name}.bak.{time.time_ns()}'
                dst.rename(backup)
            shutil.copytree(src, dst)
    else:
        print('No ~/.agents/skills — skills stay in', prefix / 'skills')

print(f'Installed to {prefix}')
print('CLI: ~/.local/bin/agent-bench')
print('MCP: agent-bench-mcp  (stdio; see contrib/mcp/)')
PY

if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

systemctl --user daemon-reload

if [[ "$enable_services" -eq 1 ]]; then
  systemctl --user enable --now agent-bench-views.service
  systemctl --user enable agent-bench@padrao.service
  echo "Enabled agent-bench-views.service and agent-bench@padrao.service (login)."
  echo "Existing benches were not restarted."
fi

if [[ "$reload_hypr" -eq 1 ]] && command -v hyprctl >/dev/null && test -n "${HYPRLAND_INSTANCE_SIGNATURE:-}"; then
  hyprctl reload
  errors="$(hyprctl configerrors || true)"
  if test -n "$errors"; then
    echo "$errors" >&2
    exit 1
  fi
fi

echo
echo 'Next:'
echo '  agent-bench ensure demo'
echo '  agent-bench cdp demo'
echo '  Super+6 … Super+0 to visit workspaces 6–10 (workspace 11 from the panel)'
echo
echo 'Point Cursor / Claude / Codex at agent-bench-mcp. Examples in contrib/mcp/'
echo 'Docs: docs/install.md'
echo 'This is isolation of display and input, not a security sandbox.'
