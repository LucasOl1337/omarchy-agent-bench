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
import os
import re
import shutil
import sys
import time

root, prefix, install_skills, reload_hypr = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3] == '1', sys.argv[4] == '1'
home = Path.home()
prefix = prefix.expanduser().absolute()
if any(path.is_symlink() for path in (prefix, *prefix.parents)) or (prefix / 'desktop').is_symlink():
    raise SystemExit('Refusing a symlinked install root or desktop. Keep runtime state in place.')
prefix = prefix.resolve()

if prefix.resolve() == root.resolve():
    raise SystemExit('Refusing to install into the source tree. Pick another --prefix.')

prefix.mkdir(parents=True, exist_ok=True)
persistent = ('sessions', 'browser-seed')

def copy_code(src, dst):
    copied = shutil.copy2(src, dst)
    path = Path(copied)
    data = path.read_bytes()
    if b'@PREFIX@' in data:
        path.write_bytes(data.replace(b'@PREFIX@', str(prefix).encode()))
    return copied

def copy_skill(src, dst):
    copied = shutil.copy2(src, dst)
    path = Path(copied)
    if path.suffix == '.md':
        # Only hub copies move away from the skills/ + docs/ source layout.
        def doc_link(match):
            target = (Path(src).parent / match[1]).resolve()
            if not target.is_relative_to(prefix / 'docs'):
                return match[0]
            relative = os.path.relpath(target, path.parent.resolve())
            return f'](<{relative}{match[2] or ""}>)'
        body = path.read_text(encoding='utf-8')
        relocated = re.sub(r'\]\((\.\./\.\./docs/[^()\s<>#]+\.md)(#[^()\s<>]*)?\)', doc_link, body)
        if relocated != body:
            path.write_text(relocated, encoding='utf-8')
    return copied

for name in ('bin', 'desktop', 'contrib', 'skills', 'docs'):
    src, dst = root / name, prefix / name
    if name == 'desktop' and dst.is_dir():
        # Persistent entries may be profiles, links or uncertain state. Do not
        # open, traverse, replace or remove them during a code installation.
        for child in dst.iterdir():
            if child.name in persistent:
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    elif dst.is_dir():
        # With prefix ~/.agents these directories are shared with other tools:
        # skills/ is the machine's skill hub and bin/ holds agent-hub. Replacing
        # them whole wiped both on 23/09/2026. Touch only this project's entries
        # and leave links owned by other sources alone.
        for child in src.iterdir():
            if child.name == '__pycache__' or child.suffix == '.pyc':
                continue
            target = dst / child.name
            if target.is_symlink():
                print(f'{name}: {target} is a link owned by another source; kept')
                continue
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            if child.is_dir():
                shutil.copytree(child, target, copy_function=copy_code,
                                ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                copy_code(child, target)
        continue
    elif dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, dirs_exist_ok=True, copy_function=copy_code,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', *persistent))

bindir = home / '.local/bin'
bindir.mkdir(parents=True, exist_ok=True)
for name in ('agent-bench', 'agent-bench-mcp', 'agent-bench-web', 'agent-bench-profile', 'agent-bench-native'):
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
            shutil.copytree(src, dst, copy_function=copy_skill)
    else:
        print('No ~/.agents/skills — skills stay in', prefix / 'skills')

print(f'Installed to {prefix}')
print('CLI: ~/.local/bin/agent-bench')
print('MCP: agent-bench-mcp  (stdio; see contrib/mcp/)')
PY

if command -v update-desktop-database >/dev/null; then
  update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

if [[ "$enable_services" -eq 1 ]]; then
  systemctl --user daemon-reload
  systemctl --user enable --now agent-bench-views.service
  systemctl --user enable agent-bench@padrao.service
  echo "Enabled agent-bench-views.service and agent-bench@padrao.service (login)."
  echo "Existing benches were not restarted."
fi

if [[ "$enable_services" -eq 1 && "$reload_hypr" -eq 1 ]] && command -v hyprctl >/dev/null && test -n "${HYPRLAND_INSTANCE_SIGNATURE:-}"; then
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
echo '  agent-bench doctor demo'
echo '  Browser profiles need an offline agent seed: docs/profile-provisioning.md'
echo '  Human takeover: agent-bench visit demo (workspace shortcuts depend on local bindings)'
echo
echo 'Point Cursor / Claude / Codex at agent-bench-mcp. Examples in contrib/mcp/'
echo 'Docs: docs/install.md'
echo 'This is isolation of display and input, not a security sandbox.'
