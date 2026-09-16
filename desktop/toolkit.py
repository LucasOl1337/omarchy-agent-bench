"""Discover installed tools and inspect one bench without exposing credentials."""
import shutil
from pathlib import Path

from bench_ops import cdp_snapshot

BASE = Path(__file__).resolve().parent

BASE = Path(__file__).resolve().parent
TOOLS = {
    'Navegação e interface': [('chromium', 'Navegador com perfil exclusivo: agent-bench browser NOME'), ('cua-driver', 'Mouse, teclado e visão: agent-bench-mcp ou agent-bench mcp NOME'), ('playwright', 'CDP da bancada: agent-bench cdp NOME'), ('xdotool', 'Entrada X11: somente via agent-bench exec'), ('xclip', 'Clipboard isolado: agent-bench clipboard NOME'), ('xterm', 'Terminal: agent-bench terminal NOME')],
    'Código e dados': [('python', 'Scripts e análise'), ('node', 'JavaScript e ferramentas web'), ('git', 'Controle de versões'), ('rg', 'Buscar texto'), ('fd', 'Localizar arquivos'), ('jq', 'Consultar JSON'), ('curl', 'HTTP e APIs'), ('sqlite3', 'Consultar bancos SQLite'), ('nvim', 'Editar arquivos no terminal')],
    'Imagens e documentos': [('ffmpeg', 'Vídeo e áudio'), ('tesseract', 'OCR de imagens'), ('magick', 'Processar imagens'), ('pdftotext', 'Extrair texto de PDF'), ('pdftoppm', 'Renderizar páginas PDF')],
}

def catalog():
    return [{'category': category, 'name': name, 'purpose': purpose, 'path': shutil.which(name), 'available': bool(shutil.which(name))}
            for category, tools in TOOLS.items() for name, purpose in tools]

def inspect(name, info, view):
    display = info.get('display', '')
    number = display.lstrip(':').split('.')[0]
    browser = cdp_snapshot(info['state'])
    workspace = view.get('workspace')
    return {'name': name, 'display': display, 'display_socket': Path('/tmp/.X11-unix/X' + number).exists(),
            'workspace': workspace, 'workspace_reserved': workspace in range(6, 12),
            'viewer_running': bool(view.get('viewer_pid')),
            'control': info.get('control_mode', view.get('mode', 'desconhecido')),
            'clipboard_shared': info.get('clipboard_bridge', False), 'browser': browser,
            'owner': info.get('owner'),
            'tools': catalog(), 'guide': str(BASE / 'USAGE.md')}
