import tempfile
import unittest
from pathlib import Path

import bench_workspaces as bw

STATE = ('0\tHDMI-A-1\tSamsung Electric Company LS27AG32x H9JTC00567\n'
         '1\tDP-2\tASUSTek COMPUTER INC VG32VQ1B R9LMTF053035\n'
         '2\tDP-3\tASUSTek COMPUTER INC VG34WQML5A W4LMDW001467\n')


class WorkspacesTest(unittest.TestCase):
    def test_default_without_config(self):
        self.assertEqual(bw.parse(''), ((6, 7, 8, 9, 10, 11), None, 0))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(bw.load(Path(d) / 'x', Path(d) / 'y')[0], bw.DEFAULT)

    def test_absolute_range(self):
        self.assertEqual(bw.parse('# c\n26-30\n'), ((26, 27, 28, 29, 30), None, 0))

    def test_slots_follow_monitor_block(self):
        ws, mon, base = bw.parse('6-10 monitor=ASUSTek COMPUTER INC VG32VQ1B', STATE)
        self.assertEqual(base, 10)
        self.assertEqual(ws, (16, 17, 18, 19, 20))
        self.assertEqual(mon, 'ASUSTek COMPUTER INC VG32VQ1B')

    def test_monitor_without_omarchy_block_stays_absolute(self):
        self.assertEqual(bw.parse('6-10 monitor=Outro', STATE)[0], (6, 7, 8, 9, 10))

    def test_from_slot(self):
        old = bw.AGENT_WORKSPACES, bw.BASE
        try:
            bw.AGENT_WORKSPACES, bw.BASE = (16, 17, 18, 19, 20), 10
            self.assertEqual([bw.from_slot(n) for n in (6, 10, 16, 3, 11)], [16, 20, 16, None, None])
        finally:
            bw.AGENT_WORKSPACES, bw.BASE = old

    def test_invalid_falls_back(self):
        for text in ('abc', '0-3', '5-20', '9-6'):
            self.assertEqual(bw.parse(text)[0], bw.DEFAULT, text)


if __name__ == '__main__':
    unittest.main()
