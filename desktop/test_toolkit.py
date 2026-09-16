import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import bench_views as views
from toolkit import inspect, catalog

class Allocation(unittest.TestCase):
    def setUp(self):views.entries.clear()
    def test_uses_all_six_reserved_workspaces(self):
        with patch.object(views,'clients',return_value=[]),patch.object(views,'hypr',return_value='[]'):
            for expected in range(6,12):
                slot=views.pick_slot();self.assertEqual(slot,expected);views.entries[str(slot)]={'workspace':slot}
            self.assertIn(views.pick_slot(),range(6,12))
    def test_avoids_visible_or_occupied_slot_when_free_exists(self):
        with patch.object(views,'clients',return_value=[{'workspace':{'id':6}}]),patch.object(views,'hypr',return_value='[{"activeWorkspace":{"id":7}}]'):
            self.assertEqual(views.pick_slot(),8)
    def test_never_allocates_human_or_unreserved_slot(self):
        views.entries.update({str(i):{'workspace':6+i%6} for i in range(24)})
        with patch.object(views,'clients',return_value=[]),patch.object(views,'hypr',return_value='[]'):
            self.assertIn(views.pick_slot(),range(6,12))

class Diagnostics(unittest.TestCase):
    def test_closed_browser_and_unreserved_workspace_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            result=inspect('fixture',{'state':folder,'display':':999'}, {'workspace':1})
            self.assertFalse(result['workspace_reserved']);self.assertFalse(result['display_socket']);self.assertEqual(result['browser']['status'],'fechado')
    def test_invalid_endpoint_does_not_fallback_to_personal_browser(self):
        with tempfile.TemporaryDirectory() as folder:
            endpoint=Path(folder)/'chromium/DevToolsActivePort';endpoint.parent.mkdir();endpoint.write_text('invalid')
            with patch('bench_ops.build_opener') as opener:
                result=inspect('fixture',{'state':folder}, {'workspace':11})
                opener.assert_not_called();self.assertTrue(result['workspace_reserved']);self.assertIn('sem conexão',result['browser']['status'])
    def test_catalog_reports_missing_tools_honestly(self):
        with patch('toolkit.shutil.which',return_value=None):
            self.assertTrue(all(not row['available'] for row in catalog()))

if __name__=='__main__':unittest.main()
