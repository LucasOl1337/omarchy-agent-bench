"""Discover CUA schemas from the local binary, without starting a desktop or MCP."""
import copy
import json
import math
from pathlib import Path
import shutil
import subprocess


DEFAULT_TIMEOUT = 5.0
ANNOTATIONS = {
    'read_only': 'readOnlyHint',
    'destructive': 'destructiveHint',
    'idempotent': 'idempotentHint',
}


class CatalogError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


def normalize_tools(document):
    """Convert dump-docs --type mcp fields without inventing unavailable metadata.

    This checks the catalog envelope, not the full JSON Schema vocabulary.
    Operational filtering and adding the bench argument belong to the caller.
    """
    if (not isinstance(document, dict)
            or not isinstance(document.get('tools'), list)
            or not document['tools']):
        raise CatalogError('catalog_invalid', 'catálogo sem lista de ferramentas')
    result, names = [], set()
    for index, tool in enumerate(document['tools']):
        if not isinstance(tool, dict):
            raise CatalogError('catalog_invalid', f'ferramenta {index} inválida')
        name = tool.get('name')
        schema = tool.get('input_schema')
        if (not isinstance(name, str) or not name or name in names
                or not isinstance(tool.get('description'), str)
                or not isinstance(schema, dict) or schema.get('type') != 'object'):
            raise CatalogError('catalog_invalid', f'ferramenta {index} incompleta ou duplicada')
        annotations = {}
        for source, target in ANNOTATIONS.items():
            if source in tool:
                if type(tool[source]) is not bool:
                    raise CatalogError('catalog_invalid', f'anotação inválida na ferramenta {index}')
                annotations[target] = tool[source]
        normalized = {'name': name, 'description': tool['description'],
                      'inputSchema': copy.deepcopy(schema)}
        if annotations:
            normalized['annotations'] = annotations
        names.add(name)
        result.append(normalized)
    return result


def load_cua_tools(*, timeout=DEFAULT_TIMEOUT):
    """Run only the static docs command. Errors never trigger desktop discovery.

    No file-based catalog or cache is accepted: each call describes the binary
    currently resolved in PATH. Timeout limits the local dump process.
    """
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError('timeout deve ser positivo e finito')
    binary = shutil.which('cua-driver')
    if binary is None:
        raise CatalogError('catalog_unavailable', 'cua-driver não encontrado no PATH')
    try:
        executable = str(Path(binary).resolve(strict=True))
        completed = subprocess.run(
            [executable, 'dump-docs', '--type', 'mcp'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CatalogError('catalog_timeout', 'prazo do dump estático excedido') from exc
    except (OSError, RuntimeError) as exc:
        raise CatalogError('catalog_unavailable', 'não foi possível executar o dump estático') from exc
    if completed.returncode:
        raise CatalogError('catalog_failed', f'dump estático terminou com código {completed.returncode}')
    try:
        document = json.loads(completed.stdout)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogError('catalog_invalid', 'dump estático não contém JSON válido') from exc
    return normalize_tools(document)
