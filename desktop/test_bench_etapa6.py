"""Etapa 6 da v2: GPU integrada por padrão, Chromium fora da NVIDIA, instalação com versão, fila automática."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import bench_fila as fila
import bench_versao as versao

CLI = runpy.run_path(str(Path(__file__).resolve().parent.parent / 'bin/agent-bench'))


def fake_drm(root, cards):
    """cards: [(renderD, driver, vram_bytes or None)]"""
    drm = root / 'drm'
    for node, driver, vram in cards:
        device = root / 'devices' / node
        (root / 'drivers' / driver).mkdir(parents=True, exist_ok=True)
        device.mkdir(parents=True)
        (device / 'driver').symlink_to(root / 'drivers' / driver)
        if vram is not None:
            (device / 'mem_info_vram_total').write_text(str(vram))
            (device / 'mem_info_vram_used').write_text(str(vram // 8))
        (drm / node).mkdir(parents=True)
        (drm / node / 'device').symlink_to(device)
    return drm


class GpuTests(unittest.TestCase):
    def pick(self, cards, env=None):
        render_node = CLI['render_node']
        with tempfile.TemporaryDirectory() as temp:
            drm = fake_drm(Path(temp), cards)
            real_path = Path

            def fake_path(*parts):
                text = str(real_path(*parts))
                return real_path(text.replace('/sys/class/drm', str(drm), 1))
            with patch.dict(render_node.__globals__, {'Path': fake_path}), \
                    patch.object(os, 'access', return_value=True):
                return render_node(env or {})

    def test_integrated_amd_never_nvidia(self):
        self.assertEqual(self.pick([('renderD128', 'nvidia', None), ('renderD129', 'amdgpu', 2 << 30)]),
                         '/dev/dri/renderD129')

    def test_discrete_amd_and_nvidia_only_mean_software(self):
        self.assertEqual(self.pick([('renderD128', 'nvidia', None), ('renderD129', 'amdgpu', 16 << 30)]), '')

    def test_intel_and_explicit_choice(self):
        self.assertEqual(self.pick([('renderD128', 'i915', None)]), '/dev/dri/renderD128')
        self.assertEqual(self.pick([('renderD128', 'i915', None)], {'AGENT_BENCH_RENDER_NODE': 'software'}), '')

    def test_bench_env_only_changes_gl(self):
        env = {'VK_DRIVER_FILES': 'nvidia_icd.json', 'LIBVA_DRIVER_NAME': 'nvidia', '__GLX_VENDOR_LIBRARY_NAME': 'nvidia'}
        with patch.dict(CLI['gpu_env'].__globals__, {'render_node': lambda e: ''}):
            out = CLI['gpu_env'](dict(env))
        self.assertEqual((out['__GLX_VENDOR_LIBRARY_NAME'], out['LIBGL_ALWAYS_SOFTWARE']), ('mesa', '1'))
        self.assertEqual(out['VK_DRIVER_FILES'], 'nvidia_icd.json')  # Unreal may still use the 4070

    def test_chromium_env_drops_nvidia(self):
        session = {'VK_DRIVER_FILES': '/x/nvidia_icd.json', 'VK_ICD_FILENAMES': '/x/nvidia_icd.json',
                   'VK_LOADER_DRIVERS_DISABLE': 'radeon_icd.json', 'LIBVA_DRIVER_NAME': 'nvidia',
                   'NVD_BACKEND': 'direct', 'AGENT_BENCH_RENDER_NODE': ''}
        out = CLI['browser_env'](session)
        self.assertNotIn('nvidia', json.dumps(out))
        self.assertNotIn('LIBVA_DRIVER_NAME', out)
        self.assertNotIn('NVD_BACKEND', out)
        self.assertEqual(session['LIBVA_DRIVER_NAME'], 'nvidia')  # caller's env untouched


class VersaoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo, self.base = root / 'repo', root / 'agents'
        (self.repo / 'bin').mkdir(parents=True)
        (self.repo / 'desktop').mkdir()
        (self.repo / 'bin' / 'tool').write_text('#!/bin/sh\necho 1\n')
        (self.repo / 'bin' / 'tool').chmod(0o755)
        (self.repo / 'desktop' / 'a.py').write_text('PREFIX = "@PREFIX@"\n')
        (self.repo / 'desktop' / 'test_a.py').write_text('x\n')
        git = lambda *a: subprocess.run(['git', '-C', str(self.repo), *a], check=True, capture_output=True)
        git('init', '-q'); git('add', '-A')
        git('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'one')
        self.git = git
        (self.base / 'desktop').mkdir(parents=True)
        (self.base / 'desktop' / 'local.txt').write_text('meu\n')
        self.patches = [patch.object(versao, 'BASE', self.base),
                        patch.object(versao, 'MANIFEST', self.base / 'desktop' / 'VERSAO.json'),
                        patch.object(versao, 'BACKUPS', self.base / '.versoes')]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def test_install_record_and_refuse_hand_edit(self):
        result = versao.update('HEAD', repo=self.repo, fetch=False)
        self.assertEqual(sorted(result['changed']), ['bin/tool', 'desktop/a.py'])
        self.assertIn(str(self.base), (self.base / 'desktop/a.py').read_text())
        self.assertTrue(os.access(self.base / 'bin/tool', os.X_OK))
        self.assertFalse((self.base / 'desktop/test_a.py').exists())
        self.assertEqual((self.base / 'desktop/local.txt').read_text(), 'meu\n')
        self.assertEqual(versao.hand_edits(), [])
        (self.base / 'desktop/a.py').write_text('remendo\n')
        with self.assertRaisesRegex(RuntimeError, 'INSTALACAO_REMENDADA'):
            versao.update('HEAD', repo=self.repo, fetch=False)
        forced = versao.update('HEAD', repo=self.repo, force=True, fetch=False)
        self.assertEqual(forced['forced_over'], ['desktop/a.py'])
        self.assertEqual((Path(forced['backup']) / 'desktop/a.py').read_text(), 'remendo\n')

    def test_report_marks_stale_benches(self):
        versao.update('HEAD', repo=self.repo, fetch=False)
        commit = versao.installed_version()
        report = versao.report([{'name': 'f3', 'version': commit}, {'name': 'f4', 'version': 'velho'}])
        self.assertEqual(report['stale'], ['f4'])


class FilaGpuTests(unittest.TestCase):
    def test_amd_limits_without_nvidia(self):
        with patch.object(fila, '_vram_nvidia', return_value=None), \
                patch.object(fila, '_vram_amd', return_value=(1500, 2048)), \
                patch.object(fila, 'VAGAS', None), patch.object(fila, 'VRAM_LIVRE_MIN', None):
            self.assertEqual(fila._limites(), (1, 512))
            self.assertEqual(fila._vram_livre(), 1500)
        with patch.object(fila, '_vram_nvidia', return_value=12000), \
                patch.object(fila, 'VAGAS', None), patch.object(fila, 'VRAM_LIVRE_MIN', None):
            self.assertEqual(fila._limites(), (2, 9000))


class FilaAutoTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        for bench in ('render', 'calma', 'manual'):
            (root / 'run' / bench).mkdir(parents=True)
            (root / 'run' / bench / 'control.sock').touch()
        self.heavy = {'render': ['UnrealEditor'], 'calma': [], 'manual': ['blender']}
        self.patches = [patch.object(fila, 'FILE', root / 'fila.json'), patch.object(fila, 'RUNTIME', root / 'run'),
                        patch.object(fila, '_cota'), patch.object(fila, '_jogando', return_value=[]),
                        patch.object(fila, '_vram_livre', return_value=None), patch.object(fila, 'VAGAS', 2),
                        patch.object(fila, 'SUBIDA_S', 0),
                        patch.object(fila, 'pesados', lambda b: self.heavy[b])]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.temp.cleanup()

    def state(self):
        return json.loads(fila.FILE.read_text())

    def test_enters_alone_and_leaves_alone_but_never_touches_manual(self):
        fila.entrar('manual', 'agente pediu')
        changed = fila.automatico()
        self.assertEqual(changed, ['render: com vaga'])
        vagas = {v['bench']: v for v in self.state()['vagas']}
        self.assertTrue(vagas['render']['auto'])
        self.assertNotIn('auto', vagas['manual'])
        self.heavy['render'] = []
        self.heavy['manual'] = []
        self.assertEqual(fila.automatico(), ['render: liberada'])
        self.assertEqual([v['bench'] for v in self.state()['vagas']], ['manual'])


if __name__ == '__main__':
    unittest.main()
