import functools
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import bench_profile as profile


class ProfilePreparation(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='bench-profile-fixture-',
            dir=os.environ.get('AGENT_BENCH_PROFILE_FS_TEST_DIR'))
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.source = self.base / 'desktop/browser-seed/chromium'
        self.target = self.base / 'desktop/sessions/test-profile/chromium'
        self.proc = self.base / 'proc'
        self.proc.mkdir()
        self.make_profile(self.source)

    def make_profile(self, root):
        root.mkdir(parents=True, mode=0o700)
        (root / 'Default').mkdir(mode=0o700)
        (root / 'Local State').write_text('{"fixture":true}')
        (root / 'Default/Preferences').write_text('{"fixture":true}')
        (root / 'Default/Cookies').write_bytes(b'fixture-session-not-a-real-cookie')

    def prepare(self, apply=False):
        return profile.prepare('test-profile', apply, base=self.base, proc_root=self.proc, from_seed=True)

    def fake_process(self, arguments, pid='87654', parent='1'):
        proc = self.proc / pid
        proc.mkdir()
        (proc / 'stat').write_text(f'{pid} (fixture) S {parent} ' + '0 ' * 17 + '123 0\n')
        (proc / 'cmdline').write_bytes(b'\0'.join(os.fsencode(arg) for arg in arguments) + b'\0')
        (proc / 'fd').mkdir()
        return proc

    def assert_refused(self, code, apply=True):
        with self.assertRaises(profile.ProfileError) as caught:
            self.prepare(apply)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def assert_no_partial_profile(self):
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.target.parent.glob('.chromium-prepare-*')), [])

    def test_dry_run_creates_no_directories_and_leaves_seed_unchanged(self):
        before = {str(p.relative_to(self.base)): p.lstat() for p in self.base.rglob('*')}
        result = self.prepare()
        after = {str(p.relative_to(self.base)): p.lstat() for p in self.base.rglob('*')}
        self.assertEqual(before.keys(), after.keys())
        for name in before:
            self.assertEqual(profile.signature(before[name]), profile.signature(after[name]))
        self.assertEqual(result['status'], 'would_prepare')
        self.assertFalse(result['changed'])
        self.assertFalse((self.base / 'desktop/sessions').exists())

    def test_inventory_sees_files_created_after_directory_was_opened(self):
        destination = self.base / 'new-tree'
        destination.mkdir(mode=0o700)
        fd = os.open(destination, profile.DIR_FLAGS)
        try:
            for index in range(8):
                (destination / str(index)).write_text('fixture')
            self.assertEqual(set(profile.inventory(fd)), {'', *(str(i) for i in range(8))})
        finally:
            os.close(fd)

    def test_first_copy_is_private_independent_complete_and_skips_cdp_hint(self):
        (self.source / 'DevToolsActivePort').write_text('9999\n/fixture\n')
        copy = profile.copy_file
        def check_not_published(*args):
            self.assertFalse(self.target.exists())
            return copy(*args)
        with patch.object(profile, 'copy_file', side_effect=check_not_published):
            result = self.prepare(True)
        self.assertEqual(result['status'], 'prepared')
        self.assertEqual(result['authentication'], 'not_verified')
        self.assertEqual(result['files_copied'], 3)
        self.assertFalse((self.target / 'DevToolsActivePort').exists())
        for relative in ('Local State', 'Default/Preferences', 'Default/Cookies'):
            source, destination = self.source / relative, self.target / relative
            self.assertEqual(source.read_bytes(), destination.read_bytes())
            self.assertNotEqual(source.stat().st_ino, destination.stat().st_ino)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o700)
        (self.target / 'Default/Cookies').write_text('renewed-in-this-bench')
        self.assertEqual((self.source / 'Default/Cookies').read_bytes(), b'fixture-session-not-a-real-cookie')

    def test_existing_profile_is_preserved_even_when_seed_is_missing(self):
        self.make_profile(self.target)
        (self.target / 'Default/Cookies').write_text('renewed')
        before = (self.target / 'Default/Cookies').stat()
        shutil.rmtree(self.source)
        for apply in (False, True):
            result = self.prepare(apply)
            self.assertEqual(result['status'], 'preserved')
            self.assertFalse(result['changed'])
        self.assertEqual((self.target / 'Default/Cookies').read_text(), 'renewed')
        self.assertEqual(profile.signature(before), profile.signature((self.target / 'Default/Cookies').stat()))

    def test_incomplete_existing_destination_is_preserved(self):
        self.target.mkdir(parents=True, mode=0o700)
        marker = self.target / 'unsaved'
        marker.write_text('keep')
        self.assert_refused('destination_invalid')
        self.assertEqual(list(self.target.iterdir()), [marker])
        self.assertEqual(marker.read_text(), 'keep')

    def test_empty_seed_does_not_create_identity(self):
        (self.source / 'Local State').unlink()
        self.assert_refused('profile_io_error')
        self.assert_no_partial_profile()

    def test_source_process_without_lock_is_refused(self):
        self.fake_process(['chromium', '--user-data-dir=' + str(self.source)])
        self.assert_refused('profile_active')
        self.assert_no_partial_profile()

    def test_destination_process_without_lock_is_refused(self):
        self.make_profile(self.target)
        self.fake_process(['chromium', '--user-data-dir', str(self.target)])
        self.assert_refused('profile_active')
        self.assertTrue((self.target / 'Default/Cookies').is_file())

    def test_source_open_database_is_refused_without_browser_name_or_flag(self):
        proc = self.fake_process(['fixture-db-writer'])
        (proc / 'fd/3').symlink_to(self.source / 'Default/Cookies')
        self.assert_refused('profile_active')
        self.assert_no_partial_profile()

    def test_unknown_process_identity_is_refused(self):
        self.fake_process(['fixture'])
        with patch.object(profile, 'process_identity', side_effect=PermissionError('private')):
            self.assert_refused('process_indeterminate')
        self.assert_no_partial_profile()

    def test_any_lock_including_stale_or_foreign_is_preserved_and_refused(self):
        for lock_name in profile.LOCKS:
            with self.subTest(lock=lock_name):
                lock = self.source / lock_name
                lock.symlink_to('foreign-host-99999999')
                self.assert_refused('profile_locked')
                self.assertEqual(os.readlink(lock), 'foreign-host-99999999')
                lock.unlink()  # fixture cleanup only, never runtime behavior
        self.assert_no_partial_profile()

    def test_destination_lock_is_never_removed(self):
        self.make_profile(self.target)
        lock = self.target / 'SingletonLock'
        lock.symlink_to('fixture-123')
        self.assert_refused('profile_locked')
        self.assertTrue(lock.is_symlink())

    def test_source_symlink_and_hardlink_refused(self):
        external = self.base / 'external'
        external.write_text('outside')
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                link = self.source / 'Default/escape'
                if kind == 'symlink':
                    link.symlink_to(external)
                else:
                    os.link(external, link)
                self.assert_refused('profile_entry_unsafe')
                link.unlink()
        self.assertEqual(external.read_text(), 'outside')
        self.assert_no_partial_profile()

    def test_source_fifo_is_refused_without_blocking(self):
        os.mkfifo(self.source / 'Default/pipe')
        self.assert_refused('profile_entry_unsafe')
        self.assert_no_partial_profile()

    def test_destination_parent_symlink_cannot_escape(self):
        outside = self.base / 'outside'
        outside.mkdir()
        sessions = self.base / 'desktop/sessions'
        sessions.mkdir()
        self.target.parent.symlink_to(outside, target_is_directory=True)
        for apply in (False, True):
            self.assert_refused('profile_io_error', apply)
        self.assertEqual(list(outside.iterdir()), [])

    def test_source_root_symlink_cannot_escape(self):
        outside = self.base / 'outside'
        self.source.rename(outside)
        self.source.symlink_to(outside, target_is_directory=True)
        self.assert_refused('profile_io_error')
        self.assert_no_partial_profile()

    def test_nonprivate_seed_is_refused_without_chmod(self):
        self.source.chmod(0o755)
        self.assert_refused('profile_permissions')
        self.assertEqual(stat.S_IMODE(self.source.stat().st_mode), 0o755)
        self.assert_no_partial_profile()

    def test_source_root_replaced_during_copy_is_refused(self):
        copy = profile.copy_file
        def replace(*args):
            result = copy(*args)
            if not (self.base / 'moved-seed').exists():
                self.source.rename(self.base / 'moved-seed')
                self.make_profile(self.source)
            return result
        with patch.object(profile, 'copy_file', side_effect=replace):
            self.assert_refused('source_changed')
        self.assert_no_partial_profile()

    def test_process_state_transition_is_not_pid_reuse(self):
        self.fake_process(['fixture'])
        with patch.object(profile, 'process_identity', side_effect=[('R', '123'), ('S', '123')]):
            self.assertEqual(self.prepare()['status'], 'would_prepare')

    def test_pid_reuse_is_refused(self):
        self.fake_process(['fixture'])
        with patch.object(profile, 'process_identity', side_effect=[('S', '123'), ('S', '456')]):
            self.assert_refused('process_indeterminate')

    def test_only_confirmed_session_infrastructure_can_have_protected_fds(self):
        manager = self.fake_process(['/usr/lib/systemd/systemd', '--user'])
        pam = self.fake_process(['(sd-pam)'], pid='87655', parent=manager.name)
        group = f'0::/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/init.scope\n'
        for process in (manager, pam):
            (process / 'cgroup').write_text(group)
            (process / 'fd/0').symlink_to('/dev/null')
        with patch.object(profile.os, 'readlink', side_effect=PermissionError('fixture protected fd')):
            result = self.prepare()
        self.assertEqual(result['process_check']['protected_infrastructure'], 2)
        self.assertEqual(result['process_check']['processes_checked'], 2)

    def test_systemd_name_without_verified_parent_or_cgroup_does_not_bypass(self):
        process = self.fake_process(['/usr/lib/systemd/systemd', '--user'], parent='2')
        (process / 'cgroup').write_text('0::/unrelated.scope\n')
        (process / 'fd/0').symlink_to('/dev/null')
        with patch.object(profile.os, 'readlink', side_effect=PermissionError('fixture protected fd')):
            self.assert_refused('process_indeterminate')

    def test_common_app_with_protected_fds_still_refuses(self):
        process = self.fake_process(['ordinary-app'])
        (process / 'cgroup').write_text('0::/unrelated.scope\n')
        (process / 'fd/0').symlink_to('/dev/null')
        with patch.object(profile.os, 'readlink', side_effect=PermissionError('fixture protected fd')):
            self.assert_refused('process_indeterminate')

    def test_browser_with_protected_fds_still_refuses(self):
        process = self.fake_process(['chromium', '--user-data-dir=/tmp/another-profile'])
        (process / 'cgroup').write_text('0::/unrelated.scope\n')
        (process / 'fd/0').symlink_to('/dev/null')
        readlink = os.readlink
        def protected(path, *args, **kwargs):
            if str(path).startswith(str(process / 'fd')):
                raise PermissionError('fixture protected fd')
            return readlink(path, *args, **kwargs)
        with patch.object(profile.os, 'readlink', side_effect=protected):
            self.assert_refused('process_indeterminate')

    def test_copy_failure_removes_only_own_stage(self):
        with patch.object(profile, 'copy_file', side_effect=OSError('fixture disk full')):
            self.assert_refused('profile_io_error')
        self.assert_no_partial_profile()
        self.assertTrue((self.source / 'Default/Cookies').exists())

    def test_source_change_during_copy_prevents_publication(self):
        copy = profile.copy_file
        def mutate(*args):
            result = copy(*args)
            (self.source / 'Default/Preferences').write_text('{"changed":true}')
            return result
        with patch.object(profile, 'copy_file', side_effect=mutate):
            self.assert_refused('source_changed')
        self.assert_no_partial_profile()

    def test_process_start_during_copy_prevents_publication(self):
        copy = profile.copy_file
        def start(*args):
            result = copy(*args)
            if not (self.proc / '87654').exists():
                self.fake_process(['chromium', '--user-data-dir=' + str(self.source)])
            return result
        with patch.object(profile, 'copy_file', side_effect=start):
            self.assert_refused('profile_active')
        self.assert_no_partial_profile()

    def test_corrupt_staged_copy_is_not_published(self):
        copy = profile.copy_file
        def corrupt(source, destination):
            digest = copy(source, destination)
            os.lseek(destination, 0, os.SEEK_SET)
            os.write(destination, b'corrupt')
            return digest
        with patch.object(profile, 'copy_file', side_effect=corrupt):
            self.assert_refused('copy_invalid')
        self.assert_no_partial_profile()

    def test_atomic_publish_preserves_destination_created_during_copy(self):
        publish = profile.publish
        def race(parent, stage):
            self.target.mkdir(mode=0o700)
            return publish(parent, stage)
        with patch.object(profile, 'publish', side_effect=race):
            self.assert_refused('destination_exists')
        self.assertEqual(list(self.target.iterdir()), [])
        self.assertEqual(list(self.target.parent.glob('.chromium-prepare-*')), [])

    def test_output_does_not_include_file_contents_or_raw_exception(self):
        output = io.StringIO()
        seeded = functools.partial(profile.prepare, from_seed=True)
        with patch.object(profile, 'BASE', self.base), patch.object(profile, 'PROC', self.proc), \
                patch.object(profile, 'copy_file', side_effect=OSError('SECRET_FIXTURE_VALUE')), \
                patch.object(profile, 'prepare', seeded), \
                patch('sys.stdout', output):
            self.assertEqual(profile.main(['prepare', 'test-profile', '--apply']), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result['status'], 'refused')
        self.assertNotIn('SECRET_FIXTURE_VALUE', output.getvalue())
        self.assertNotIn('fixture-session', output.getvalue())

    def test_actual_cli_with_fixture_installation(self):
        repo = Path(__file__).resolve().parent.parent
        (self.base / 'bin').mkdir()
        for name in ('bench_profile.py', 'bench_ops.py', 'bench_control.py'):
            shutil.copyfile(repo / 'desktop' / name, self.base / 'desktop' / name)
        # This fixture exercises the real CLI/parser and filesystem transaction,
        # but must not race unrelated processes on the host. Override only this
        # temporary module's /proc dependency, just as the in-process cases do.
        # The production CLI deliberately exposes no override for this guard.
        with (self.base / 'desktop/bench_profile.py').open('a') as module:
            module.write('\nPROC = Path(' + repr(str(self.proc)) + ')\n')
        command = self.base / 'bin/agent-bench-profile'
        shutil.copyfile(repo / 'bin/agent-bench-profile', command)
        for options, expected in (([], 'would_prepare_empty'), (['--apply'], 'prepared_empty'), (['--apply'], 'preserved')):
            result = subprocess.run([sys.executable, str(command), 'prepare', 'test-profile', *options],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], expected)


if __name__ == '__main__':
    unittest.main()
