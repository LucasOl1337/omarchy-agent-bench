"""Open a mission tab on the machine that already holds its WhatsApp session."""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from bench_ops import valid
from bench_web import WHATSAPP_URL


def account_routes(config, account, project):
    accounts = config.get('accounts', {})
    if not account and project:
        account = config.get('projects', {}).get(project)
    if not account and len(accounts) == 1:
        account = next(iter(accounts))
    if not account:
        raise ValueError('whatsapp_account_required: informe a conta ou associe uma ao projeto.')
    if account not in accounts or not accounts[account]:
        raise ValueError('whatsapp_account_unregistered: conta sem bancada cadastrada.')
    return account, [
        {'host': valid(route.get('host', 'local')), 'bench': valid(route['bench'])}
        for route in accounts[account]
    ]


def bench_command(host, *argv):
    command = list(argv)
    if host != 'local':
        command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', '--', host, shlex.join(command)]
    env = os.environ.copy()
    if env.get('AGENT_BENCH_NAME'):
        # The calling bench gives its own apps private XDG paths. The routing
        # commands still need the host's workspace configuration.
        for key in ('XDG_CONFIG_HOME', 'XDG_STATE_HOME'):
            env.pop(key, None)
    result = subprocess.run(command, capture_output=True, text=True, timeout=65, env=env)
    if result.returncode:
        # Browser commands can return tab state on stdout. Keep that local.
        try:
            detail = json.loads(result.stdout).get('error')
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(detail or f'{argv[0]} falhou em {host} (código {result.returncode})')
    return json.loads(result.stdout)


def open_route(route, mission):
    host, bench = route['host'], route['bench']
    prefix = ('agent-bench-web', '--bench', bench, '--mission', mission)
    try:
        browser = bench_command(host, 'agent-bench', 'cdp', bench)
        if browser.get('status') != 'conectado' or browser.get('lives_in') != bench:
            raise RuntimeError('Chromium da bancada indisponível.')
        bench_command(host, *prefix, 'hygiene')
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        return {'state': 'unreachable', **route, 'reason': str(exc)}
    try:
        opened = bench_command(host, *prefix, 'open', '--url', WHATSAPP_URL)
    except RuntimeError as exc:
        if str(exc).startswith('whatsapp_busy:'):
            return {'state': 'busy', **route}
        raise
    tab = opened['tab']
    session = 'unconfirmed'
    try:
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            observed = bench_command(host, *prefix, 'whatsapp-state', '--tab', tab)['state']
            if observed in ('paired', 'pairing_required', 'busy', 'wrong_origin'):
                session = observed
                break
            time.sleep(2)
    finally:
        if session != 'paired' and not opened.get('reused'):
            bench_command(host, *prefix, 'close', '--tab', tab)
            bench_command(host, *prefix, 'hygiene')
    if session == 'paired':
        return {'state': session, **route, 'tab': tab, 'mission': mission}
    return {'state': session, **route}


def open_whatsapp(config, mission, account=None, project=None):
    mission = valid(mission)
    account, routes = account_routes(config, account, project)
    attempts = []
    for route in routes:
        # Never retry an uncertain open on another machine. It may have made a
        # window that now needs reconciliation by this mission.
        result = open_route(route, mission)
        if result['state'] == 'paired':
            return {'account': account, **result}
        attempts.append(result)
    state = 'pairing_required' if all(item['state'] == 'pairing_required' for item in attempts) else 'unavailable'
    return {'account': account, 'state': state, 'routes': attempts}


def main(argv):
    parser = argparse.ArgumentParser(prog='agent-bench whatsapp', description=__doc__)
    parser.add_argument('mission')
    parser.add_argument('--account')
    parser.add_argument('--project')
    args = parser.parse_args(argv)
    config_home = None if os.environ.get('AGENT_BENCH_NAME') else os.environ.get('XDG_CONFIG_HOME')
    directory = Path(config_home) if config_home else Path.home() / '.config'
    try:
        config = json.loads((directory / 'agent-bench/whatsapp.json').read_text())
        result = open_whatsapp(config, args.mission, args.account, args.project)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['state'] == 'paired' else 1
