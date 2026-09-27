"""Explicit opt-in Chromium, without a debugging or WebDriver endpoint.

This module deliberately does not use ensure/open_browser/browser_snapshot:
ensure currently inspects CDP even when browser=False. It only starts the bench,
then uses its control gate and launch RPC. No profile provisioning or recovery.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit

import bench_ops as ops
from bench_control import control

BINARY_ENV = 'AGENT_BENCH_NATIVE_BINARY'
BINARIES = ('/usr/lib/chromium/chromium', '/usr/lib64/chromium/chromium',
            '/opt/chromium/chromium')
LOCKS = ('SingletonLock', 'SingletonSocket', 'SingletonCookie')
FLAGS = ('--ozone-platform=x11', '--password-store=basic',
         '--profile-directory=Default', '--force-renderer-accessibility',
         '--no-first-run', '--no-default-browser-check')
LIMITATIONS = [
    'No CDP, WebDriver, DOM inspection, tab inventory or page-load confirmation.',
    'Accessibility requested, not proof that the native accessibility tree is available.',
    'Existing profile preserved. Authentication and restored tabs are not verified.',
    'URL delivery is at most once per invocation, not proof of navigation. Reconcile uncertain outcomes before retrying.',
    'password-store=basic retained for compatibility, not encrypted keyring storage.',
    'Chromium may erase /proc/environ. In that case display is inferred from the validated bench cgroup, not verified from process environment.',
]


class NativeBrowserError(RuntimeError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def fail(code, message, **details):
    raise NativeBrowserError(code, message, **details)


def binary_path(explicit=None):
    """Never execute PATH wrappers, including ELF wrappers in /usr/bin.

    An override selects an unpacked Chromium binary with its resources beside it.
    ELF plus resource layout is a packaging check, not a publisher attestation.
    """
    selected = explicit if explicit is not None else os.environ.get(BINARY_ENV)
    candidates = [selected] if selected is not None else BINARIES
    for candidate in candidates:
        try:
            path = Path(candidate)
            if not path.is_absolute():
                raise ValueError('absolute path required')
            path = path.resolve(strict=True)
            if path.name != 'chromium' or path.parent.name in ('bin', 'sbin'):
                raise ValueError('not the direct Chromium binary')
            if not path.is_file() or not os.access(path, os.X_OK):
                raise ValueError('not executable')
            with path.open('rb') as stream:
                if stream.read(4) != b'\x7fELF':
                    raise ValueError('not ELF')
            if not all((path.parent / item).is_file() for item in ('resources.pak', 'icudtl.dat')):
                raise ValueError('Chromium resource bundle missing')
            return path
        except (OSError, ValueError, TypeError):
            continue
    fail('binary_invalid', 'Direct Chromium ELF binary unavailable. Set --binary or '
         + BINARY_ENV + ' to the official binary beside resources.pak and icudtl.dat, not a wrapper.')


def valid_url(url):
    if url is None or url == 'about:blank':
        return url
    if not isinstance(url, str) or not url or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        fail('url_invalid', 'Use an explicit http/https URL or about:blank, without whitespace or control characters.')
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError('scheme, host or credentials')
        parsed.port
    except ValueError:
        fail('url_invalid', 'Only http/https URLs without credentials and about:blank are authorized. Files and command flags are refused.')
    return url


def prepared_profile(name):
    _, state = ops.paths(name)
    try:
        profile = ops.require_bench_profile(state)
    except RuntimeError as exc:
        fail('profile_missing', str(exc))
    # Do not follow a profile or Default symlink into the human browser/another bench.
    if state.resolve() != state.absolute() or profile.resolve() != profile.absolute() or (profile / 'Default').is_symlink():
        fail('profile_invalid', 'Bench state/profile must not redirect through symlinks.')
    return profile


def command_line(raw):
    """Chromium on some distros rewrites argv as one space-joined NUL field."""
    fields = [os.fsdecode(field) for field in raw.split(b'\0') if field]
    if len(fields) == 1:
        try:
            # URL bytes after our terminator are not shell syntax. A legal URL
            # can contain an apostrophe or quote, and must not hide a switch.
            prefix, separator, positional = fields[0].partition(' -- ')
            fields = shlex.split(prefix)
            if separator:
                fields.extend(['--', positional])
        except ValueError:
            fail('process_uncertain', 'Cannot unambiguously parse the process command line.')
    if not fields:
        fail('process_uncertain', 'Empty process command line.')
    return fields


def switches(argv):
    result = {}
    index = 1
    while index < len(argv):
        value = argv[index]
        if value == '--':
            break
        # base::CommandLine on Linux also accepts a single '-' switch prefix.
        if value.startswith('-') and value != '-':
            value = '--' + value.lstrip('-')
            key, sep, arg = value.partition('=')
            if not sep and index + 1 < len(argv) and not argv[index + 1].startswith('-'):
                arg = argv[index + 1]
                index += 1
            if key in result:
                fail('process_uncertain', 'Duplicate Chromium switch: ' + key)
            result[key] = arg
        index += 1
    return result


def process_stamp(directory):
    fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
    if fields[0] in ('Z', 'X'):
        fail('process_uncertain', 'Browser process is no longer live.')
    return int(fields[19])  # starttime, /proc stat field 22


def bench_info(name):
    try:
        return ops.request(name, {'action': 'status'}, timeout=1)
    except (OSError, RuntimeError, ValueError):
        return None


def validate_bench(name, info):
    _, state = ops.paths(name)
    if (not isinstance(info, dict) or info.get('name') != name
            or info.get('state') != str(state)
            or not re.fullmatch(r':(?:8[0-9]|9[0-9]|1[0-9]{2})', str(info.get('display', '')))):
        fail('bench_uncertain', 'Named bench state and isolated X11 display could not be confirmed.')
    return info


def inspect_process(name, profile, binary, proc, info):
    location = ops.process_location(proc)
    if location != name:
        fail('browser_outside_bench', 'Existing profile process is outside the requested bench.', pid=proc['pid'], lives_in=location)
    validate_bench(name, info)
    directory = ops.PROC / str(proc['pid'])
    try:
        before = process_stamp(directory)
        if directory.stat().st_uid != os.getuid():
            fail('process_uncertain', 'Browser process is owned by another user.')
        executable = (directory / 'exe').resolve(strict=True)
        if executable != binary:
            fail('binary_mismatch', 'Existing browser does not use the selected direct executable.', executable=str(executable))
        argv = command_line((directory / 'cmdline').read_bytes())
        flags = switches(argv)
        forbidden = [key for key in flags if key.startswith('--remote-debugging') or key in (
            '--enable-automation', '--test-type', '--no-sandbox', '--disable-setuid-sandbox',
            '--disable-web-security', '--user-agent', '--user-agent-product',
            '--disable-blink-features', '--headless')]
        if forbidden:
            fail('browser_not_native', 'Existing Chromium has debugging, automation or unsafe/spoofing switches. Preserve it, no restart.', flags=forbidden, pid=proc['pid'])
        # DISPLAY in /proc/environ does not override an explicit Chromium
        # --display switch. Only the bench-inherited display is authorized,
        # including when Chromium has erased its environment entirely.
        if '--display' in flags:
            fail('browser_outside_bench', 'Explicit Chromium display overrides are not authorized. Use the bench-inherited display.')
        if '--ozone-platform-hint' in flags and flags['--ozone-platform-hint'] != 'x11':
            fail('browser_outside_bench', 'Chromium backend hint is not the isolated X11 backend.')
        required = {'--user-data-dir': str(profile), '--profile-directory': 'Default',
                    '--password-store': 'basic', '--ozone-platform': 'x11'}
        if any(flags.get(key) != value for key, value in required.items()) or '--type' in flags:
            fail('process_identity_mismatch', 'Browser profile or required native flags do not match this bench.')
        groups = (directory / 'cgroup').read_text().splitlines()
        service = f'agent-bench@{ops.valid(name)}.service'
        if not any(line.startswith('0::/') and service in line[3:].split('/') for line in groups):
            fail('browser_outside_bench', 'Actual PID cgroup does not contain the named bench unit.')
        raw_env = (directory / 'environ').read_bytes()
        env = dict(item.split(b'=', 1) for item in raw_env.split(b'\0') if b'=' in item)
        # Chromium's process-title initialization can zero this whole region.
        # Only a wholly erased environment permits inference from the cgroup.
        # A readable but mismatched/partial environment still fails closed.
        env_verified = bool(raw_env.strip(b'\0'))
        if env_verified and (env.get(b'DISPLAY') != info['display'].encode() or env.get(b'AGENT_BENCH_NAME') != name.encode()):
            fail('browser_outside_bench', 'Browser environment does not match the isolated display and bench.')
        if before != process_stamp(directory) or ops.profile_process(profile) != proc:
            fail('process_uncertain', 'Browser PID or singleton owner changed during inspection.')
        return {'pid': proc['pid'], 'start_time_ticks': before, 'lives_in': location,
                'executable': str(executable), 'user_data_dir': str(profile),
                'profile_directory': 'Default', 'display': info['display'],
                'display_source': 'process_environment' if env_verified else 'bench_status/cgroup',
                'env_verified': env_verified,
                'cgroup': next(line[3:] for line in groups if line.startswith('0::/')),
                'flags_verified': True, 'accessibility_requested': '--force-renderer-accessibility' in flags}
    except NativeBrowserError:
        raise
    except (OSError, ValueError, IndexError, RuntimeError) as exc:
        fail('process_uncertain', 'Could not confirm the real Chromium PID identity. Preserve the session.', reason=str(exc))


def no_unowned_process(profile):
    """A missing singleton lock is not proof that the profile is unused."""
    for directory in ops.PROC.iterdir():
        if not directory.name.isdigit():
            continue
        try:
            if directory.stat().st_uid != os.getuid():
                continue
            raw = (directory / 'cmdline').read_bytes()
            if not raw or b'-user-data-dir' not in raw:
                continue
            # Agent prompts and shell scripts can mention Chromium switches.
            # Only a browser executable can own this Chromium profile.
            executable = os.readlink(directory / 'exe').removesuffix(' (deleted)')
            if Path(executable).name not in ('chromium', 'chrome', 'chromium-browser'):
                continue
            args = command_line(raw)
            flags = switches(args)
            value = flags.get('--user-data-dir')
            if value and Path(value).resolve() == profile.resolve():
                fail('profile_owner_uncertain', 'Process references this profile without a validated singleton owner.', pid=int(directory.name))
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            fail('process_uncertain', 'Cannot inspect a same-user process to exclude an unowned profile process.')


def browser_identity(name, profile, binary, info):
    try:
        proc = ops.profile_process(profile)
    except RuntimeError as exc:
        fail('profile_owner_uncertain', str(exc))
    if proc:
        return inspect_process(name, profile, binary, proc, info)
    locks = [item for item in LOCKS if os.path.lexists(profile / item)]
    if locks:
        fail('profile_lock_uncertain', 'Unowned/stale singleton artifacts exist. Preserve them, do not launch or remove locks.', locks=locks)
    no_unowned_process(profile)
    return None


def result(name, profile, identity, info=None):
    runtime, _ = ops.paths(name)
    return {'ok': True, 'mode': 'native-opt-in', 'bench': name,
            'status': 'running' if identity else 'closed',
            'prepared': True, 'user_data_dir': str(profile),
            'control_mode': 'humano' if (runtime / 'human-control').exists() else (info or {}).get('control_mode'),
            'browser': identity, 'capabilities': {'cdp': False, 'webdriver': False,
                'dom': False, 'tabs': False, 'native_input': bool(identity),
                'accessibility_requested': bool(identity and identity['accessibility_requested']),
                'accessibility_verified': False}, 'limitations': LIMITATIONS}


def status(name, binary=None):
    """Read only: no start/ensure/control, viewer, activity or endpoint probing."""
    ops.valid(name)
    profile = prepared_profile(name)
    executable = binary_path(binary)
    info = bench_info(name)
    return result(name, profile, browser_identity(name, profile, executable, info), info)


@contextmanager
def operation_lock(runtime):
    with (runtime / 'native-browser.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('operation_in_progress', 'Another native browser operation is in progress. No launch attempted.')
        yield


def open_browser(name, url=None, binary=None, timeout=5.0):
    ops.valid(name)
    valid_url(url)
    if not math.isfinite(timeout) or not 0 < timeout <= 15:
        fail('timeout_invalid', 'Startup timeout must be greater than zero and at most 15 seconds.')
    executable = binary_path(binary)
    profile = prepared_profile(name)
    runtime, _ = ops.paths(name)
    if (runtime / 'human-control').exists():
        fail('human_control', 'The human owns this bench. Do not resume or mutate it.')
    # Refuse external/debugging browsers and ambiguous locks before even starting a bench.
    browser_identity(name, profile, executable, bench_info(name))
    info = validate_bench(name, ops.start(name))  # never ensure(), which probes CDP
    with control(name), operation_lock(runtime):
        info = validate_bench(name, ops.request(name, {'action': 'status'}, timeout=1))
        if info.get('control_mode') != 'agente':
            fail('human_control', 'Agent control could not be confirmed.')
        identity = browser_identity(name, profile, executable, info)
        if identity and url is None:
            return {**result(name, profile, identity, info), 'action': 'reused', 'url_delivery': 'not_requested'}
        argv = [str(executable), *FLAGS, '--user-data-dir=' + str(profile)]
        # The explicit terminator and URL validation both prevent option injection.
        if url is not None:
            argv.extend(['--', url])
        # No implicit blank URL: Chromium restores the profile's existing session.
        try:
            reply = ops.request(name, {'action': 'launch', 'argv': argv, 'cwd': str(profile.parent)}, timeout=2)
        except (OSError, RuntimeError, ValueError) as exc:
            fail('launch_uncertain', 'Launch reply missing. Do not restart or repeat the URL, reconcile status first.', reason=str(exc), url_delivery='uncertain' if url else 'not_requested')
        if (not isinstance(reply, dict) or type(reply.get('pid')) is not int
                or reply['pid'] <= 0 or reply.get('name') != name or reply.get('display') != info['display']):
            fail('launch_uncertain', 'Launch acknowledgement identity is invalid. Do not retry.', url_delivery='uncertain' if url else 'not_requested')
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                after = browser_identity(name, profile, executable, info)
                if after:
                    if identity and (after['pid'], after['start_time_ticks']) != (identity['pid'], identity['start_time_ticks']):
                        fail('launch_uncertain', 'Singleton owner changed after URL delivery. Preserve both processes, do not retry.')
                    if not identity and after['pid'] != reply['pid']:
                        fail('launch_uncertain', 'Launch PID does not match the singleton owner. Do not retry.')
                    return {**result(name, profile, after, info), 'action': 'url_dispatched' if identity else 'started',
                            'launch_pid': reply['pid'], 'url_delivery': 'dispatched_unverified' if url else 'not_requested'}
            except NativeBrowserError as exc:
                if exc.code not in ('profile_lock_uncertain', 'profile_owner_uncertain', 'process_uncertain'):
                    exc.details.update(launch_pid=reply['pid'],
                                       url_delivery='uncertain' if url else 'not_requested')
                    raise
                last_error = exc.code
            time.sleep(.05)
        fail('launch_timeout', 'Native PID identity not confirmed in time. Process/session preserved, no retry or termination.',
             launch_pid=reply['pid'], last_observation=last_error,
             url_delivery='uncertain' if url else 'not_requested')


class Parser(argparse.ArgumentParser):
    def error(self, message):
        fail('arguments_invalid', message)


def main(argv=None):
    parser = Parser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    for command in ('status', 'open'):
        sub = subparsers.add_parser(command)
        sub.add_argument('name')
        sub.add_argument('--binary', help='Direct official Chromium ELF, never a PATH wrapper. Env: ' + BINARY_ENV)
        if command == 'open':
            sub.add_argument('url', nargs='?', help='http/https/about:blank only. Omit to preserve/reuse the session.')
            sub.add_argument('--timeout', type=float, default=5.0, help='PID verification deadline in seconds (0 < timeout <= 15).')
    try:
        args = parser.parse_args(argv)
        output = status(args.name, args.binary) if args.command == 'status' else open_browser(args.name, args.url, args.binary, args.timeout)
    except NativeBrowserError as exc:
        output = {'ok': False, 'mode': 'native-opt-in', 'status': 'blocked',
                  'error': {'code': exc.code, 'message': str(exc), **exc.details},
                  'session_preserved': True}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        output = {'ok': False, 'mode': 'native-opt-in', 'status': 'blocked',
                  'error': {'code': 'native_error', 'message': str(exc)}, 'session_preserved': True}
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if output['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
