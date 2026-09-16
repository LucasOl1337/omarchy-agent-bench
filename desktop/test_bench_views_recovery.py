import unittest
from unittest.mock import patch, Mock
import bench_views as v

class ViewerRecovery(unittest.TestCase):
    def setUp(self):
        v.entries.clear(); v.processes.clear()
        v.entries['test'] = {'workspace': 6, 'viewer_pid': 123, 'launched': True}
    def probe(self, code):
        p=Mock();p.poll.return_value=code;p.returncode=code;v.processes['test']=p
        with patch.object(v,'live',return_value={}),patch.object(v,'save'):
            return v.ensure('test',reopen=False)
    def test_crash_is_scheduled_for_recovery(self):
        r=self.probe(1)
        self.assertFalse(r['launched']);self.assertIsNone(r['viewer_pid']);self.assertGreater(r['retry_at'],v.time.monotonic())
    def test_normal_close_stays_closed(self):
        r=self.probe(0)
        self.assertTrue(r['launched']);self.assertIsNone(r['viewer_pid'])
    def test_signal_exit_is_recovered(self):
        self.assertFalse(self.probe(-11)['launched'])
    def test_live_viewer_is_preserved(self):
        self.assertEqual(self.probe(None)['viewer_pid'],123)

if __name__ == '__main__': unittest.main()
