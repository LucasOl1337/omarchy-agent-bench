"""Which Hyprland workspaces belong to agent benches.

One source for the supervisor, the CLI, the web guard and the Hyprland rules
(contrib/omarchy/agent-bench.lua reads the same files). Without a config the
benches keep the classic 6-11.

Omarchy 4.5 gives each monitor its own block of ten workspaces (1-10, 11-20,
21-30), assigned per physical monitor in ``~/.local/state/omarchy/monitor-workspaces``.
To pin the benches to one monitor, write ``$XDG_CONFIG_HOME/agent-bench/workspaces``::

    6-10 monitor=ASUSTek COMPUTER INC VG32VQ1B

The slots are relative to that monitor's block: if Omarchy gave it 11-20, the
benches use 16-20, and Super+6...0 on that monitor reach them. Without the
monitor (or before Omarchy has assigned its block) the numbers are absolute.
"""
import os
from pathlib import Path
import re

DEFAULT = (6, 7, 8, 9, 10, 11)
SLOTS = 10
CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'agent-bench' / 'workspaces'
STATE = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local' / 'state')) / 'omarchy' / 'monitor-workspaces'
LINE = re.compile(r'^\s*(\d+)\s*-\s*(\d+)(?:\s+monitor=(.+?))?\s*$')


def block_of(monitor, state_text):
    """Omarchy's block for a monitor whose identity starts with ``monitor``."""
    for line in (state_text or '').splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[0].isdigit() and parts[2].startswith(monitor):
            return int(parts[0])
    return None


def parse(text, state_text=''):
    """Return (workspaces, monitor, base) from the config text, or the default.

    ``base`` is the first id of the monitor's block minus one, so slot N
    (what Super+N means under per-monitor workspaces) is workspace base + N."""
    for line in (text or '').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        match = LINE.match(line)
        if not match:
            break
        first, last, monitor = int(match.group(1)), int(match.group(2)), match.group(3)
        if not 1 <= first <= last or last - first >= SLOTS:
            break
        base = 0
        if monitor:
            block = block_of(monitor, state_text)
            if block is not None and last <= SLOTS:
                base = block * SLOTS
        return tuple(range(base + first, base + last + 1)), monitor, base
    return DEFAULT, None, 0


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ''


def load(config=None, state=None):
    return parse(read(config or CONFIG), read(state or STATE))


AGENT_WORKSPACES, MONITOR, BASE = load()


def from_slot(number):
    """Workspace for ``agent-bench visit N``: an id, or a slot of the bench monitor."""
    if number in AGENT_WORKSPACES:
        return number
    if BASE and BASE + number in AGENT_WORKSPACES:
        return BASE + number
    return None


def label():
    return f'{AGENT_WORKSPACES[0]}–{AGENT_WORKSPACES[-1]}'
