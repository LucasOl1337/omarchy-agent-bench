"""Installer staging only: fake home lookup and commands, no desktop/services."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


class InstallStaging(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='agent-bench-install-test-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.source = self.root / 'source'
        shutil.copytree(SOURCE, self.source, ignore=shutil.ignore_patterns(
            '.git', '__pycache__', '*.pyc', 'sessions', 'browser-seed', 'artifacts'))
        self.home = self.root / 'fixture-home'
        self.home.mkdir()
        self.prefix = self.root / 'installed'
        self.commands = self.root / 'commands'
        self.commands.mkdir()
        self.log = self.root / 'commands.jsonl'
        self.env = os.environ.copy()
        self.env.update(PATH=str(self.commands), PYTHONDONTWRITEBYTECODE='1',
                        AGENT_BENCH_INSTALL_TEST_HOME=str(self.home),
                        AGENT_BENCH_INSTALL_TEST_LOG=str(self.log))
        self.env.pop('AGENT_BENCH_PREFIX', None)
        self.env.pop('PYTHONPATH', None)
        for name in ('DISPLAY', 'WAYLAND_DISPLAY', 'HYPRLAND_INSTANCE_SIGNATURE',
                     'DBUS_SESSION_BUS_ADDRESS', 'XDG_RUNTIME_DIR'):
            self.env.pop(name, None)
        # Do not replace HOME. Only the installer Python's Path.home lookup is
        # mocked; shell side effects are intercepted by executables below.
        python = '''import os,pathlib,runpy,sys
pathlib.Path.home=classmethod(lambda cls:pathlib.Path(os.environ['AGENT_BENCH_INSTALL_TEST_HOME']))
sys.argv=sys.argv[1:]
if sys.argv[0]=='-':
 exec(compile(sys.stdin.read(),'<staged-installer>','exec'),{'__name__':'__main__'})
elif sys.argv[0]=='-c':
 exec(compile(sys.argv[1],'<staged-command>','exec'),{'__name__':'__main__'})
else:
 runpy.run_path(sys.argv[0],run_name='__main__')
'''
        self.executable('python3', python)
        (self.commands / 'dirname').symlink_to(shutil.which('dirname'))
        for name in ('systemctl', 'update-desktop-database', 'hyprctl', 'systemd-run', 'Xvnc', 'chromium'):
            self.executable(name, '''import json,os,pathlib,sys
name=pathlib.Path(sys.argv[0]).name
with open(os.environ['AGENT_BENCH_INSTALL_TEST_LOG'],'a') as out:
 out.write(json.dumps([name,*sys.argv[1:]])+'\\n')
sys.exit(int(os.environ.get('AGENT_BENCH_INSTALL_TEST_SYSTEMCTL_EXIT','0')) if name=='systemctl' else (0 if name=='update-desktop-database' else 97))
''')
        self.executable('cua-driver', '''import json,os,sys
with open(os.environ['AGENT_BENCH_INSTALL_TEST_LOG'],'a') as out:
 out.write(json.dumps(['cua-driver',*sys.argv[1:]])+'\\n')
if sys.argv[1:]!=['dump-docs','--type','mcp']:
 sys.exit(97)
print(json.dumps({'tools':[{'name':'click','description':'fixture native tool','input_schema':{'type':'object'}},{'name':'page','description':'unbound fixture tool','input_schema':{'type':'object'}}]}))
''')

    def executable(self, name, body):
        path = self.commands / name
        path.write_text('#!' + sys.executable + '\n' + body)
        path.chmod(0o755)

    def install(self, prefix=None, enable=False, hypr=False, skills=False):
        return subprocess.run(['/bin/bash', str(self.source / 'install.sh'), '--prefix', str(prefix or self.prefix),
                               *([] if enable else ['--no-enable']),
                               *([] if skills else ['--no-skills']),
                               *([] if hypr else ['--no-hypr'])], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def command(self, name, *args, input=None):
        return subprocess.run([str(self.home / '.local/bin' / name), *args], input=input,
                              env=self.env, cwd=self.root, capture_output=True, text=True, timeout=5)

    def test_copy_only_install_does_not_contact_manager_or_display(self):
        self.env['AGENT_BENCH_INSTALL_TEST_SYSTEMCTL_EXIT'] = '71'
        result = self.install()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(any(call[0] in ('systemctl', 'hyprctl', 'systemd-run', 'Xvnc', 'chromium') for call in self.calls()))

    def test_skills_docs_links_resolve_from_hub_with_default_and_spaced_prefixes(self):
        hub = self.home / '.agents/skills'
        hub.mkdir(parents=True)
        fixture = self.source / 'skills/agent-bench/SKILL.md'
        unchanged = ('[External](https://example.invalid/../../docs/install.md#outside)\n'
                     '[Local](other.md)\nLiteral ../../docs/install.md\n')
        fixture.write_text(fixture.read_text() + '\n' + unchanged +
                           '[Fixture label](../../docs/install.md#configuration)\n')
        asset = fixture.with_name('fixture.txt')
        asset.write_text('[Not Markdown](../../docs/install.md)\n')
        originals = {path.relative_to(self.source / 'skills'): path.read_bytes()
                     for path in (self.source / 'skills').rglob('*') if path.is_file()}
        for prefix in (self.home / '.local/share/omarchy-agent-bench',
                       self.root / 'custom prefix with spaces'):
            with self.subTest(prefix=prefix.name):
                result = self.install(prefix, skills=True)
                self.assertEqual(0, result.returncode, result.stderr)
                destinations = []
                for relative, original in originals.items():
                    self.assertEqual(original, (self.source / 'skills' / relative).read_bytes())
                    self.assertEqual(original, (prefix / 'skills' / relative).read_bytes())
                    installed = hub / relative
                    if installed.suffix != '.md':
                        self.assertEqual(original, installed.read_bytes())
                        continue
                    text = installed.read_text()
                    self.assertNotIn('](../../docs/', text)
                    for destination in re.findall(r'\]\(<([^>]+)>\)', text):
                        target = (installed.parent / destination.split('#', 1)[0]).resolve()
                        self.assertEqual(prefix / 'docs', target.parent)
                        self.assertTrue(target.is_file(), destination)
                        destinations.append(destination)
                self.assertGreaterEqual(len(destinations), 6)
                installed_fixture = hub / 'agent-bench/SKILL.md'
                expected = os.path.relpath(prefix / 'docs/install.md', installed_fixture.parent)
                self.assertIn(f'[Fixture label](<{expected}#configuration>)', installed_fixture.read_text())
                self.assertIn(unchanged, installed_fixture.read_text())
                self.assertFalse((self.home / '.agents/docs').exists())
        self.assertFalse(any(call[0] in ('systemctl', 'hyprctl', 'systemd-run', 'Xvnc', 'chromium')
                             for call in self.calls()))

    def test_skills_install_preserves_existing_backups_and_unrelated_hub_entries(self):
        hub = self.home / '.agents/skills'
        old = hub / 'agent-bench/SKILL.md'
        old.parent.mkdir(parents=True)
        old.write_bytes(b'Existing skill [docs](../../docs/install.md)\n')
        old_state = old.read_bytes(), old.stat().st_ino
        preserved = {}
        for name in ('agent-bench.bak.existing', 'human-owned-skill'):
            marker = hub / name / 'SKILL.md'
            marker.parent.mkdir()
            marker.write_text(name)
            preserved[marker] = marker.read_bytes(), marker.stat().st_ino
        result = self.install(skills=True)
        self.assertEqual(0, result.returncode, result.stderr)
        backups = [path for path in hub.glob('agent-bench.bak.*') if path.name != 'agent-bench.bak.existing']
        self.assertEqual(1, len(backups))
        backup = backups[0] / 'SKILL.md'
        self.assertEqual(old_state, (backup.read_bytes(), backup.stat().st_ino))
        for marker, state in preserved.items():
            self.assertEqual(state, (marker.read_bytes(), marker.stat().st_ino))
        self.assertNotEqual(old_state[0], old.read_bytes())

    def test_prefix_on_shared_agents_dir_keeps_other_skills(self):
        # 23/09/2026: install.sh with prefix ~/.agents replaced the whole shared hub.
        prefix = self.home / '.agents'
        hub = prefix / 'skills'
        other = hub / 'tdd/SKILL.md'
        other.parent.mkdir(parents=True)
        other.write_text('outra fonte')
        source = self.root / 'central/human-agent-coexistence'
        source.mkdir(parents=True)
        (source / 'SKILL.md').write_text('fonte central')
        (hub / 'human-agent-coexistence').symlink_to(source, target_is_directory=True)
        result = self.install(prefix)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('outra fonte', other.read_text())
        self.assertTrue((hub / 'human-agent-coexistence').is_symlink())
        self.assertEqual('fonte central', (source / 'SKILL.md').read_text())
        self.assertTrue((hub / 'agent-bench/SKILL.md').is_file())

    def test_prefix_on_shared_agents_dir_keeps_other_commands(self):
        prefix = self.home / '.agents'
        hub_cli = prefix / 'bin/agent-hub'
        hub_cli.parent.mkdir(parents=True)
        hub_cli.write_text('#!/bin/sh\n')
        result = self.install(prefix)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('#!/bin/sh\n', hub_cli.read_text())
        self.assertTrue((prefix / 'bin/agent-bench').is_file())

    def test_normal_install_keeps_expected_manager_calls_using_only_fake_systemctl(self):
        result = self.install(enable=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([
            ['systemctl', '--user', 'daemon-reload'],
            ['systemctl', '--user', 'enable', '--now', 'agent-bench-views.service'],
            ['systemctl', '--user', 'enable', 'agent-bench@padrao.service']],
            [call for call in self.calls() if call[0] == 'systemctl'])

    def test_copy_only_may_copy_hypr_config_but_never_reload_a_running_session(self):
        config = self.home / '.config/hypr/hyprland.lua'
        config.parent.mkdir(parents=True)
        config.write_text('-- fixture config\n')
        self.env['HYPRLAND_INSTANCE_SIGNATURE'] = 'fixture-only'
        result = self.install(hypr=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('require("hypr.agent-bench")', config.read_text())
        self.assertFalse(any(call[0] in ('systemctl', 'hyprctl') for call in self.calls()))

    def test_reinstall_preserves_runtime_profiles_and_does_not_copy_source_profiles(self):
        first = self.install()
        self.assertEqual(0, first.returncode, first.stderr)
        sentinels = {}
        for relative in ('sessions/task/chromium/Default/sentinel', 'sessions/task/.keep',
                         'browser-seed/chromium/Default/sentinel'):
            path = self.prefix / 'desktop' / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            data = b'fixture-persistent-state\x00@PREFIX@\xff'
            path.write_bytes(data)
            path.chmod(0o600)
            sentinels[path] = (data, path.stat().st_ino)
        for folder in ('sessions', 'browser-seed'):
            source_data = self.source / 'desktop' / folder / 'must-not-distribute'
            source_data.parent.mkdir(parents=True, exist_ok=True)
            source_data.write_text('artificial profile fixture')
        result = self.install()
        self.assertEqual(0, result.returncode, result.stderr)
        for path, (data, inode) in sentinels.items():
            self.assertTrue(path.exists(), f'installer removed persistent {path.relative_to(self.prefix)}')
            self.assertEqual(data, path.read_bytes())
            self.assertEqual(inode, path.stat().st_ino)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
        for folder in ('sessions', 'browser-seed'):
            self.assertFalse((self.prefix / 'desktop' / folder / 'must-not-distribute').exists())

    def test_relative_prefix_creates_absolute_working_command_links(self):
        result = self.install('relative-install')
        self.assertEqual(0, result.returncode, result.stderr)
        for name in ('agent-bench', 'agent-bench-mcp', 'agent-bench-web', 'agent-bench-profile'):
            link = self.home / '.local/bin' / name
            self.assertTrue(link.readlink().is_absolute(), f'{name}: relative symlink breaks installed entry point')
            self.assertTrue(link.exists())

    def test_reinstall_preserves_unknown_state_and_profile_links_without_following_them(self):
        self.assertEqual(0, self.install().returncode)
        outside = self.root / 'outside-profile'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_bytes(b'outside-state@PREFIX@')
        seed = self.prefix / 'desktop/browser-seed'
        seed.symlink_to(outside, target_is_directory=True)
        sessions = self.prefix / 'desktop/sessions'
        for kind in ('file', 'dangling-link'):
            with self.subTest(kind=kind):
                if kind == 'file':
                    sessions.write_bytes(b'unknown-state@PREFIX@')
                else:
                    sessions.symlink_to(self.root / 'missing-external-path')
                original = sessions.lstat().st_ino
                result = self.install()
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(original, sessions.lstat().st_ino)
                self.assertTrue(seed.is_symlink())
                self.assertEqual(outside, seed.readlink())
                self.assertEqual(b'outside-state@PREFIX@', sentinel.read_bytes())
                self.assertEqual(['sentinel'], [path.name for path in outside.iterdir()])
                if kind == 'file':
                    self.assertEqual(b'unknown-state@PREFIX@', sessions.read_bytes())
                else:
                    self.assertFalse(sessions.exists())
                sessions.unlink()

    def test_symlinked_desktop_is_refused_without_installing_through_it(self):
        self.prefix.mkdir()
        outside = self.root / 'outside-desktop'
        outside.mkdir()
        sentinel = outside / 'sentinel'
        sentinel.write_text('untouched')
        (self.prefix / 'desktop').symlink_to(outside, target_is_directory=True)
        result = self.install()
        self.assertNotEqual(0, result.returncode)
        self.assertIn('Refusing', result.stderr)
        self.assertEqual(['sentinel'], [path.name for path in outside.iterdir()])
        self.assertEqual('untouched', sentinel.read_text())
        self.assertTrue((self.prefix / 'desktop').is_symlink())

    def test_symlinked_prefix_ancestor_is_refused_before_writing_outside(self):
        outside = self.root / 'outside-install'
        outside.mkdir()
        redirect = self.root / 'redirect'
        redirect.symlink_to(outside, target_is_directory=True)
        result = self.install(redirect / 'installation')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('Refusing', result.stderr)
        self.assertEqual([], list(outside.iterdir()))

    def test_installed_entry_points_import_and_hub_discovers_without_a_desktop(self):
        result = self.install()
        self.assertEqual(0, result.returncode, result.stderr)
        for name in ('agent-bench', 'agent-bench-web', 'agent-bench-profile'):
            result = self.command(name, '--help')
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('usage:', result.stdout.lower())
        result = self.command('agent-bench', 'tools')
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(json.loads(result.stdout))
        result = self.command('agent-bench-profile', 'prepare', 'stage-smoke')
        self.assertEqual(0, result.returncode)
        self.assertEqual('would_prepare_empty', json.loads(result.stdout)['status'])
        self.assertFalse((self.prefix/'desktop/sessions').exists())
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':'generic-fixture','version':'1'}}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'}]
        result = self.command('agent-bench-mcp', input=''.join(json.dumps(msg)+'\n' for msg in messages))
        self.assertEqual(0, result.returncode, result.stderr)
        replies = list(map(json.loads, result.stdout.splitlines()))
        self.assertEqual([1,2], [reply['id'] for reply in replies])
        names = {tool['name'] for tool in replies[1]['result']['tools']}
        self.assertTrue({'bench_web','bench_ensure','click'} <= names)
        self.assertNotIn('page', names)
        self.assertFalse(any(call[0] in ('hyprctl','systemd-run','Xvnc','chromium') for call in self.calls()))
        self.assertEqual([['cua-driver','dump-docs','--type','mcp']], [call for call in self.calls() if call[0]=='cua-driver'])
        for module in ('bench_catalog','bench_native','bench_profile','bench_transport','bench_web','bench_hub_mcp','bench_mcp'):
            self.assertTrue((self.prefix/'desktop'/f'{module}.py').is_file())
        script = 'import sys;sys.path.insert(0,' + repr(str(self.prefix/'desktop')) + ');import bench_catalog,bench_native,bench_profile,bench_transport,bench_web,bench_hub_mcp,bench_mcp'
        imported = subprocess.run([str(self.commands/'python3'), '-c', script],
                                  env=self.env,cwd=self.root,capture_output=True,text=True,timeout=5)
        self.assertEqual(0, imported.returncode, imported.stderr)

    def test_installed_hub_without_cua_returns_explicit_catalog_error(self):
        result = self.install()
        self.assertEqual(0, result.returncode, result.stderr)
        (self.commands/'cua-driver').unlink()
        result = self.command('agent-bench-mcp', input=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/list'})+'\n')
        self.assertEqual(0, result.returncode, result.stderr)
        reply = json.loads(result.stdout)
        self.assertIn('catalog_unavailable', reply['error']['message'])
        self.assertFalse(any(call[0] in ('systemd-run','Xvnc','chromium') for call in self.calls()))


if __name__ == '__main__':
    unittest.main()
