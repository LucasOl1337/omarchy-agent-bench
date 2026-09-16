#!/usr/bin/env bash
# Remove the user install. Does not delete Chromium profiles under desktop/sessions.
set -euo pipefail

prefix="${AGENT_BENCH_PREFIX:-$HOME/.local/share/omarchy-agent-bench}"

usage() {
  cat <<'EOF'
Remove the agent-bench user install.

  --prefix DIR     Same prefix used at install (default: ~/.local/share/omarchy-agent-bench)
  --purge-sessions Also delete saved Chromium profiles under PREFIX/desktop/sessions
  -h, --help
EOF
}

purge=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --prefix) prefix="$2"; shift 2 ;;
    --purge-sessions) purge=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

systemctl --user disable --now agent-bench-views.service 2>/dev/null || true
# Stop only the template unit if idle; leave running named benches unless the user stops them.
systemctl --user disable agent-bench@padrao.service 2>/dev/null || true
systemctl --user daemon-reload || true

rm -f "$HOME/.local/bin/agent-bench" "$HOME/.local/bin/agent-bench-mcp"
rm -f "$HOME/.local/share/applications/agent-bench.desktop"
rm -f "$HOME/.config/systemd/user/agent-bench@.service"
rm -f "$HOME/.config/systemd/user/agent-bench-views.service"

if [[ -f "$HOME/.config/hypr/hyprland.lua" ]]; then
  python3 - <<'PY'
from pathlib import Path
main = Path.home() / '.config/hypr/hyprland.lua'
text = main.read_text()
needle = '\nrequire("hypr.agent-bench")\n'
if needle in text:
    main.write_text(text.replace(needle, '\n'))
    print('Removed require("hypr.agent-bench") from hyprland.lua')
PY
fi

if [[ "$purge" -eq 1 ]]; then
  rm -rf "$prefix"
  echo "Removed $prefix including saved sessions."
else
  echo "Left $prefix in place (profiles/cookies). Pass --purge-sessions to delete it."
fi

echo 'Named benches that were already running stay up until you: agent-bench stop NAME'
echo 'Restore ~/.config/hypr/agent-bench.lua from a .bak.* file if you customized it.'
