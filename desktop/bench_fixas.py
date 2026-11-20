"""As bancadas fixas: uma por Super+F1…F5, perfil guardado e desktop sempre de pé.

Decisão do Lucas (19/11/2026): 5 bancadas fixas que guardam o login entre tarefas;
o agente pega a primeira livre; bancada nova só com as livres ocupadas ou teste
que pede perfil vazio. Sem uso, fecha só o Chromium; o cofre nunca fecha.

Config em ``$XDG_CONFIG_HOME/agent-bench/fixas``, uma por linha, na ordem das
teclas (a primeira é Super+F1). ``livre`` no fim da linha marca as que qualquer
agente pode pegar pelo nome ``livre``::

    cofre
    dailywork-campanhas
    f3 livre
    f4 livre
    f5 livre

Sem o arquivo não há fixas e tudo funciona como antes.
"""
import os
from pathlib import Path
import re

CONFIG = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'agent-bench' / 'fixas'
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,39}')
ALIAS = 'livre'


def parse(text):
    """[{'name', 'slot', 'pool'}] in key order; malformed lines are skipped."""
    found, seen = [], set()
    for line in (text or '').splitlines():
        parts = line.split('#', 1)[0].split()
        if not parts or not NAME.fullmatch(parts[0]) or parts[0] in seen or parts[0] == ALIAS:
            continue
        seen.add(parts[0])
        found.append({'name': parts[0], 'slot': len(found), 'pool': parts[1:2] == ['livre']})
    return found


def load():
    try:
        return parse(CONFIG.read_text())
    except OSError:
        return []


def names():
    return [item['name'] for item in load()]


def pool():
    return [item['name'] for item in load() if item['pool']]


def slot_of(name):
    """Index of the bench's Super+F key (0 for F1), or None for a non-fixed bench."""
    for item in load():
        if item['name'] == name:
            return item['slot']
    return None
