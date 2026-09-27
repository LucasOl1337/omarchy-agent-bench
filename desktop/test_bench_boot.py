"""A private bench's accessibility activation must not depend on the human bus."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


class BootAccessibility(unittest.TestCase):
    def test_boot_overrides_inherited_broker_and_drops_human_accessibility_address(self):
        module = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'bin/agent-bench'))
        boot = module['boot']
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(boot.__globals__, {'paths': lambda name: (root / 'runtime', root / 'state')}), \
                 patch.dict(os.environ, {'ATSPI_DBUS_IMPLEMENTATION': 'dbus-broker',
                                         'AT_SPI_BUS_ADDRESS': 'unix:path=/human/bus',
                                         'DISPLAY': ':0', 'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/human/session'}), \
                 patch.object(os, 'execve') as execute:
                boot('test')
                executable, argv, env = execute.call_args.args
                self.assertEqual('/usr/bin/dbus-run-session', executable)
                self.assertEqual('dbus-daemon', env['ATSPI_DBUS_IMPLEMENTATION'])
                self.assertNotIn('AT_SPI_BUS_ADDRESS', env)
                self.assertNotIn('DISPLAY', env)
                self.assertNotIn('DBUS_SESSION_BUS_ADDRESS', env)
                self.assertEqual(str(root / 'runtime/run'), env['XDG_RUNTIME_DIR'])
                self.assertEqual('dbus-broker', os.environ['ATSPI_DBUS_IMPLEMENTATION'])


if __name__ == '__main__':
    unittest.main()
