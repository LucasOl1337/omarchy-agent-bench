"""No input devices: ioctl probes use fd=-1 or /dev/null, in owned children."""
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

import bench_ops as ops
import bench_uinput_guard as guard

HELPER = Path(guard.__file__).resolve()
PROBE = '''import ctypes,json,os
libc=ctypes.CDLL(None,use_errno=True)
libc.syscall.restype=ctypes.c_long
rows=[]
null=os.open('/dev/null',os.O_RDONLY)
try:
 for label,fd in [('invalid',-1),('null',null)]:
  for command in (0x5501,0x100005501,0xffffffff00005501,0x5502,0x541b):
   ctypes.set_errno(0)
   result=libc.syscall(ctypes.c_long(16),ctypes.c_long(fd),ctypes.c_ulonglong(command),ctypes.c_void_p())
   rows.append({'fd':label,'command':command,'result':result,'errno':ctypes.get_errno()})
finally:
 os.close(null)
print(json.dumps({'ioctls':rows,'no_new_privs':[line.split(':')[1].strip() for line in open('/proc/self/status') if line.startswith('NoNewPrivs:')][0]}))
'''


@unittest.skipUnless(sys.platform == 'linux' and os.uname().machine == 'x86_64',
                     'real filter proof requires the validated Linux x86_64 ABI')
class RealUinputGuard(unittest.TestCase):
    def probe(self, protected, code=PROBE):
        target = [sys.executable, '-I', '-S', '-c', code]
        command = [sys.executable, '-I', '-S', str(HELPER), *target] if protected else target
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(guard.READY_MARKER + '\n' if protected else '', result.stderr)
        return json.loads(result.stdout)

    def test_ioctl_denial_survives_exec_and_only_matches_create_request(self):
        baseline = self.probe(False)
        protected = self.probe(True)
        self.assertEqual('1', protected['no_new_privs'])
        for before, after in zip(baseline['ioctls'], protected['ioctls']):
            with self.subTest(fd=after['fd'], command=hex(after['command'])):
                self.assertEqual(-1, before['result'])
                self.assertEqual(-1, after['result'])
                kernel_errno = errno.EBADF if before['fd'] == 'invalid' else errno.ENOTTY
                self.assertEqual(kernel_errno, before['errno'])
                if after['command'] & 0xFFFFFFFF == guard.UI_DEV_CREATE:
                    self.assertEqual(errno.EPERM, after['errno'])
                else:
                    self.assertEqual(kernel_errno, after['errno'])
        # The parent/sibling remains unchanged; a filter was never loaded here.
        self.assertEqual(baseline, self.probe(False))

    def test_descendant_exec_also_inherits_denial(self):
        child = 'import subprocess,sys;result=subprocess.run([sys.executable,"-I","-S","-c",' + repr(PROBE) + '],check=True,capture_output=True,text=True);print(result.stdout,end="")'
        protected = self.probe(True, child)
        self.assertEqual('1', protected['no_new_privs'])
        self.assertTrue(all(row['errno'] == errno.EPERM for row in protected['ioctls']
                            if row['command'] & 0xFFFFFFFF == guard.UI_DEV_CREATE))


class UinputGuardFailures(unittest.TestCase):
    def library(self):
        library = MagicMock()
        library.seccomp_arch_native.return_value = guard.SCMP_ARCH_X86_64
        library.seccomp_syscall_resolve_name.return_value = 16
        library.seccomp_init.return_value = 123
        for name in ('seccomp_attr_set', 'seccomp_rule_add_array', 'seccomp_load'):
            getattr(library, name).return_value = 0
        return library

    def refusal(self):
        stderr = io.StringIO()
        with patch.object(guard.os, 'execv') as execute, patch.object(guard.sys, 'stderr', stderr):
            self.assertEqual(126, guard.main(['/fixture/cua-driver', 'mcp', '--direct']))
            execute.assert_not_called()
        self.assertIn('CUA_UINPUT_GUARD_FAILED', stderr.getvalue())

    def test_missing_library_fails_before_exec(self):
        with patch.object(guard.ctypes, 'CDLL', side_effect=OSError('fixture missing lib')):
            self.refusal()

    def test_missing_symbol_fails_before_exec(self):
        with patch.object(guard.ctypes, 'CDLL', return_value=object()):
            self.refusal()

    def test_unsupported_abi_fails_before_loading_library_or_exec(self):
        with patch.object(guard.sys, 'byteorder', 'big'), patch.object(guard, '_load_seccomp') as load:
            self.refusal()
            load.assert_not_called()

    def test_architecture_syscall_and_allocation_fail_closed(self):
        for method, failure in (('seccomp_arch_native', 0), ('seccomp_syscall_resolve_name', -1),
                                ('seccomp_init', None)):
            with self.subTest(method=method):
                library = self.library()
                getattr(library, method).return_value = failure
                with patch.object(guard, '_load_seccomp', return_value=library):
                    self.refusal()
                library.seccomp_load.assert_not_called()

    def test_attribute_rule_and_load_failures_never_execute_and_release_context(self):
        for method in ('seccomp_attr_set', 'seccomp_rule_add_array', 'seccomp_load'):
            with self.subTest(method=method):
                library = self.library()
                getattr(library, method).return_value = -errno.EINVAL
                with patch.object(guard, '_load_seccomp', return_value=library), \
                     patch.object(guard, '_verify_filter') as verify:
                    self.refusal()
                    verify.assert_not_called()
                library.seccomp_release.assert_called_once_with(123)

    def test_failed_post_load_verification_never_executes(self):
        library = self.library()
        with patch.object(guard, '_load_seccomp', return_value=library), \
             patch.object(guard, '_verify_filter', side_effect=guard.GuardError('fixture self-check failed')):
            self.refusal()
        library.seccomp_load.assert_called_once_with(123)
        library.seccomp_release.assert_called_once_with(123)

    def test_filter_rule_masks_kernel_unsigned_command_and_sets_no_new_privs(self):
        library = self.library()
        with patch.object(guard, '_load_seccomp', return_value=library), \
             patch.object(guard, '_verify_filter') as verify:
            guard.install_filter()
        library.seccomp_init.assert_called_once_with(guard.SCMP_ACT_ALLOW)
        library.seccomp_attr_set.assert_called_once_with(123, guard.SCMP_FLTATR_CTL_NNP, 1)
        args = library.seccomp_rule_add_array.call_args.args
        self.assertEqual((123, guard.SCMP_ACT_ERRNO_EPERM, 16, 1), args[:4])
        comparison = ctypes.cast(args[4], ctypes.POINTER(guard.ArgCompare)).contents
        self.assertEqual((1, guard.SCMP_CMP_MASKED_EQ, 0xFFFFFFFF, 0x5501),
                         (comparison.arg, comparison.op, comparison.datum_a, comparison.datum_b))
        self.assertEqual(24, ctypes.sizeof(guard.ArgCompare))
        verify.assert_called_once_with(16)

    def test_main_installs_before_exec_and_preserves_arguments(self):
        calls = []
        arguments = ['/fixture/cua-driver', 'mcp', '--direct', '--no-overlay']
        stderr = io.StringIO()
        with patch.object(guard.sys, 'stderr', stderr), \
             patch.object(guard, 'install_filter', side_effect=lambda: calls.append('filter')), \
             patch.object(guard.os, 'execv', side_effect=lambda *args: calls.append(args)):
            self.assertEqual(0, guard.main(arguments))
        self.assertEqual(['filter', (arguments[0], arguments)], calls)
        self.assertEqual(guard.READY_MARKER + '\n', stderr.getvalue())

    def test_launcher_places_guard_before_cua_and_keeps_unit_cleanup_scope(self):
        with patch('bench_cua_env.validate_cua_env'), \
             patch.object(ops, 'systemd_env', return_value={}), \
             patch.object(ops.shutil, 'which', return_value='/fixture/cua-driver'), \
             patch.object(ops.subprocess, 'Popen') as spawn:
            ops.launch_cua('fixture', {'DISPLAY': ':fixture', 'HOME': '/fixture/home'})
        command = spawn.call_args.args[0]
        index = command.index('/usr/bin/python3')
        self.assertEqual(['/usr/bin/python3', '-I', '-S', str(ops.BASE/'desktop/bench_uinput_guard.py'),
                          '/fixture/cua-driver', 'mcp', '--direct', '--no-overlay'], command[index:])
        self.assertIn('--property=KillMode=control-group', command)
        self.assertIn('--property=BindsTo=agent-bench@fixture.service', command)
        self.assertIn('HOME=/fixture/home', command)
        self.assertIn('DISPLAY=:fixture', command)
        self.assertIn('-i', command)


if __name__ == '__main__':
    unittest.main()
