"""Offline, initial Chromium provisioning for any agent harness.

Never starts a browser or modifies an existing profile. Linux /proc is used to
refuse observable users of either tree; it is not a lock honored by Chromium.
"""
import argparse
from contextlib import contextmanager
import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile

from bench_ops import BASE, valid

PROC = Path('/proc')
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
LOCKS = frozenset(('SingletonLock', 'SingletonSocket', 'SingletonCookie'))
SKIP = frozenset(('DevToolsActivePort',))


class ProfileError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def fail(code, message):
    raise ProfileError(code, message)


@contextmanager
def directory(path, create=False):
    """Open each component without following links, including the ancestors."""
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        fail('path_invalid', 'Use a árvore absoluta esperada, sem links ou ..')
    fd = os.open('/', DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            try:
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def signature(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def list_names(fd):
    # A directory opened before population can retain an old enumeration view
    # on Btrfs. Open a fresh description of the same inode, without path lookup
    # outside our held directory or sharing the caller's stream via dup().
    fresh = os.open('.', DIR_FLAGS, dir_fd=fd)
    try:
        return os.listdir(fresh)
    finally:
        os.close(fresh)


def private_root(fd):
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        fail('profile_permissions', 'A raiz do perfil deve pertencer ao usuário e ter modo 0700.')


def no_locks(fd):
    for name in LOCKS:
        try:
            os.stat(name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        fail('profile_locked', 'Há lock de Chromium; preserve-o. O seed e o destino precisam estar offline.')


def prepared(fd):
    """Structural readiness only; it makes no authentication claim."""
    for name in ('Local State', 'Default/Preferences'):
        parent, _, leaf = name.rpartition('/')
        child = os.open(parent, DIR_FLAGS, dir_fd=fd) if parent else os.dup(fd)
        try:
            info = os.stat(leaf, dir_fd=child, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_size == 0 or info.st_nlink != 1:
                fail('profile_invalid', 'Perfil incompleto: exige Local State e Default/Preferences regulares e não vazios.')
        finally:
            os.close(child)


def inventory(fd, prefix=''):
    """Record metadata without accepting symlinks, hardlinks or special files."""
    before = os.fstat(fd)
    result = {prefix: signature(before)}
    for name in sorted(list_names(fd)):
        rel = prefix + '/' + name if prefix else name
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_uid != os.getuid():
            fail('profile_owner', 'A árvore do perfil contém outro proprietário.')
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            try:
                if signature(os.fstat(child)) != signature(info):
                    fail('source_changed', 'A origem mudou durante a inspeção; nada foi publicado.')
                result.update(inventory(child, rel))
            finally:
                os.close(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            result[rel] = signature(info)
        else:
            fail('profile_entry_unsafe', 'Links e arquivos especiais não são aceitos no perfil inicial.')
    if signature(os.fstat(fd)) != signature(before):
        fail('source_changed', 'A origem mudou durante a inspeção; nada foi publicado.')
    return result


def overlaps(path, roots):
    path = Path(path).resolve()
    return any(path == root or root in path.parents for root in roots)


def process_identity(proc):
    fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    return fields[0], fields[19]  # state and starttime distinguish PID reuse


def session_infrastructure(proc, argv):
    """Recognize only systemd --user and its sd-pam child in init.scope.

    Their proc FDs can be protected even from their own UID. Ordinary programs,
    browsers and processes merely named 'systemd' never gain this exception.
    """
    expected_group = f'/user.slice/user-{os.getuid()}.slice/user@{os.getuid()}.service/init.scope'
    def manager(candidate, arguments):
        fields = (candidate / 'stat').read_text().rsplit(')', 1)[1].split()
        return (candidate.stat().st_uid == os.getuid() and fields[1] == '1'
                and arguments in (['/usr/lib/systemd/systemd', '--user'], ['/lib/systemd/systemd', '--user'])
                and any(line.rpartition(':')[2] == expected_group for line in (candidate / 'cgroup').read_text().splitlines()))
    if manager(proc, argv):
        return True
    if argv != ['(sd-pam)'] or not any(line.rpartition(':')[2] == expected_group
                                     for line in (proc / 'cgroup').read_text().splitlines()):
        return False
    parent_id = (proc / 'stat').read_text().rsplit(')', 1)[1].split()[1]
    parent = proc.parent / parent_id
    parent_argv = [os.fsdecode(value) for value in (parent / 'cmdline').read_bytes().split(b'\0') if value]
    return manager(parent, parent_argv)


def require_idle(roots, proc_root=None):
    """Refuse ambiguous app processes; report the narrow init.scope exception."""
    roots = tuple(Path(root) for root in roots)
    coverage = {'scope': 'current_uid', 'processes_checked': 0, 'protected_infrastructure': 0}
    for proc in (proc_root or PROC).iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            identity = process_identity(proc)
            if identity[0] == 'Z':
                continue
            argv = [os.fsdecode(value) for value in (proc / 'cmdline').read_bytes().split(b'\0') if value]
            if not argv:
                fail('process_indeterminate', 'Não foi possível identificar um processo do usuário.')
            cwd = None
            for index, value in enumerate(argv):
                raw = None
                if value.startswith('--user-data-dir='):
                    raw = value.split('=', 1)[1]
                elif value == '--user-data-dir':
                    if index + 1 == len(argv):
                        fail('process_indeterminate', 'Argumento de perfil incompleto em processo ativo.')
                    raw = argv[index + 1]
                if raw is not None:
                    profile = Path(raw)
                    if not profile.is_absolute():
                        cwd = os.readlink(proc / 'cwd')
                        profile = Path(cwd) / profile
                    if overlaps(profile, roots):
                        fail('profile_active', 'Processo ativo aponta para a origem ou destino. Preserve a sessão.')
            try:
                for entry in (proc / 'fd').iterdir():
                    try:
                        target = os.readlink(entry)
                    except FileNotFoundError:  # file descriptor closed meanwhile
                        continue
                    if target.startswith('/') and overlaps(target.removesuffix(' (deleted)'), roots):
                        fail('profile_active', 'Há arquivos abertos na origem ou destino. Preserve a sessão.')
            except PermissionError:
                if not session_infrastructure(proc, argv):
                    raise
                coverage['protected_infrastructure'] += 1
            if process_identity(proc)[1] != identity[1]:
                fail('process_indeterminate', 'A identidade de um processo mudou; refaça o diagnóstico.')
            coverage['processes_checked'] += 1
        except FileNotFoundError:
            if proc.exists():
                fail('process_indeterminate', 'Inspeção de processo incompleta; nada foi publicado.')
        except (OSError, ValueError, IndexError):
            fail('process_indeterminate', 'Não foi possível verificar os processos do usuário; nada foi publicado.')
    return coverage


def copy_file(source, destination):
    digest = hashlib.sha256()
    while data := os.read(source, 1024 * 1024):
        digest.update(data)
        pending = memoryview(data)
        while pending:
            written = os.write(destination, pending)
            if written == 0:
                raise OSError('short write')
            pending = pending[written:]
    os.fsync(destination)
    return digest.hexdigest()


def copy_tree(source, destination, expected, prefix=''):
    hashes = {}
    for name in sorted(list_names(source)):
        rel = prefix + '/' + name if prefix else name
        if rel in SKIP:
            continue
        info = os.stat(name, dir_fd=source, follow_symlinks=False)
        if signature(info) != expected.get(rel):
            fail('source_changed', 'A origem mudou durante a cópia; nada foi publicado.')
        if stat.S_ISDIR(info.st_mode):
            os.mkdir(name, 0o700, dir_fd=destination)
            child_source = os.open(name, DIR_FLAGS, dir_fd=source)
            child_destination = os.open(name, DIR_FLAGS, dir_fd=destination)
            try:
                if signature(os.fstat(child_source)) != expected[rel]:
                    fail('source_changed', 'A origem mudou durante a cópia; nada foi publicado.')
                hashes.update(copy_tree(child_source, child_destination, expected, rel))
            finally:
                os.close(child_source)
                os.close(child_destination)
        else:
            src = os.open(name, FILE_FLAGS, dir_fd=source)
            try:
                if signature(os.fstat(src)) != expected[rel]:
                    fail('source_changed', 'A origem mudou durante a cópia; nada foi publicado.')
                dst = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
                try:
                    hashes[rel] = copy_file(src, dst)
                finally:
                    os.close(dst)
                if signature(os.fstat(src)) != expected[rel]:
                    fail('source_changed', 'A origem mudou durante a cópia; nada foi publicado.')
            finally:
                os.close(src)
    os.fsync(destination)
    return hashes


def verify_copy(fd, hashes, prefix=''):
    found = set()
    for name in list_names(fd):
        rel = prefix + '/' + name if prefix else name
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, DIR_FLAGS, dir_fd=fd)
            try:
                found.update(verify_copy(child, hashes, rel))
            finally:
                os.close(child)
        else:
            child = os.open(name, FILE_FLAGS, dir_fd=fd)
            with os.fdopen(child, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    fail('copy_invalid', 'Entrada inválida na cópia; nada foi publicado.')
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != hashes.get(rel):
                fail('copy_invalid', 'A verificação da cópia falhou; nada foi publicado.')
            found.add(rel)
    return found


def publish(parent, stage):
    """Atomic Linux rename that refuses even an empty preexisting destination."""
    rename = getattr(ctypes.CDLL(None, use_errno=True), 'renameat2', None)
    if rename is None:
        fail('atomic_rename_unavailable', 'renameat2 não está disponível; nada foi publicado.')
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(stage), parent, b'chromium', 1) != 0:  # RENAME_NOREPLACE
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            fail('destination_exists', 'O destino apareceu durante o preparo e foi preservado.')
        fail('atomic_rename_failed', 'Não foi possível publicar a cópia atomicamente; destino preservado.')


def prepare(name, apply=False, *, base=None, proc_root=None, from_seed=False):
    name = valid(name)
    base = Path(base or BASE)
    source = base / 'desktop/browser-seed/chromium'
    target = base / 'desktop/sessions' / name / 'chromium'
    summary = {'bench': name, 'apply': apply, 'source': str(source),
               'user_data_dir': str(target), 'authentication': 'not_verified'}
    if not from_seed:
        # Lucas, 23/09/2026: copies of the seed put the same server sessions in
        # dozens of browsers, and a logout or rotation in one killed them all.
        # Logins live only in the vault; a new profile starts empty.
        from bench_ops import provide_bench_profile
        summary.update(source=None, authentication='nenhuma: contas logadas ficam só no cofre (agent-bench cdp cofre)')
        if os.path.lexists(target):
            return {**summary, 'status': 'preserved', 'changed': False}
        if not apply:
            return {**summary, 'status': 'would_prepare_empty', 'changed': False}
        provide_bench_profile(target.parent)
        return {**summary, 'status': 'prepared_empty', 'changed': True}
    try:
        # Inspect existing destinations without reading or requiring the seed.
        try:
            with directory(target) as existing:
                private_root(existing)
                no_locks(existing)
                coverage = require_idle((target,), proc_root)
                prepared(existing)
                return {**summary, 'status': 'preserved', 'changed': False, 'process_check': coverage}
        except FileNotFoundError:
            if os.path.lexists(target):
                fail('destination_invalid', 'Destino existente incompleto; foi preservado.')
        with directory(source) as src:
            private_root(src)
            no_locks(src)
            prepared(src)
            fcntl.flock(src, fcntl.LOCK_EX | fcntl.LOCK_NB)
            coverage = require_idle((source, target), proc_root)
            expected = inventory(src)
            if not apply:
                # Also validate existing destination ancestors without creating them.
                ancestor = target.parent
                while not os.path.lexists(ancestor):
                    ancestor = ancestor.parent
                with directory(ancestor):
                    pass
                return {**summary, 'status': 'would_prepare', 'changed': False, 'process_check': coverage}
            with directory(target.parent, create=True) as parent:
                private_root(parent)
                stage_path = Path(tempfile.mkdtemp(prefix='.chromium-prepare-', dir=f'/proc/self/fd/{parent}'))
                stage = stage_path.name
                try:
                    dst = os.open(stage, DIR_FLAGS, dir_fd=parent)
                    try:
                        hashes = copy_tree(src, dst, expected)
                        if set(inventory(dst)) != set(expected) - SKIP or verify_copy(dst, hashes) != set(hashes):
                            fail('copy_invalid', 'Cópia incompleta; nada foi publicado.')
                        prepared(dst)
                    finally:
                        os.close(dst)
                    no_locks(src)
                    coverage = require_idle((source, target), proc_root)
                    if inventory(src) != expected:
                        fail('source_changed', 'A origem mudou durante a cópia; nada foi publicado.')
                    # Ensure path components still name the directories held open.
                    with directory(source) as current_source, directory(target.parent) as current_parent:
                        if any((os.fstat(a).st_dev, os.fstat(a).st_ino) !=
                               (os.fstat(b).st_dev, os.fstat(b).st_ino)
                               for a, b in ((current_source, src), (current_parent, parent))):
                            fail('path_changed', 'A árvore foi substituída durante o preparo; nada foi publicado.')
                    publish(parent, stage)
                    stage = None
                    try:
                        os.fsync(parent)
                    except OSError:
                        fail('publication_uncertain', 'Perfil publicado, mas fsync falhou; reconcilie o destino antes de retomar.')
                finally:
                    if stage is not None:
                        shutil.rmtree(stage, dir_fd=parent)
            return {**summary, 'status': 'prepared', 'changed': True,
                    'files_copied': len(hashes), 'skipped_runtime_files': sorted(SKIP),
                    'process_check': coverage}
    except ProfileError:
        raise
    except BlockingIOError:
        fail('preparation_busy', 'Outro preparo já usa esse seed; aguarde e reconcilie.')
    except OSError:
        fail('profile_io_error', 'Árvore ausente, insegura ou erro de leitura/cópia; nenhum perfil existente foi alterado.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cria o perfil vazio de uma bancada. Contas logadas ficam só no cofre.')
    commands = parser.add_subparsers(dest='command', required=True)
    command = commands.add_parser('prepare', help='Dry-run por padrão; preserva qualquer perfil existente.')
    command.add_argument('name')
    command.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = prepare(args.name, apply=args.apply)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ProfileError, ValueError) as exc:
        print(json.dumps({'status': 'refused', 'code': getattr(exc, 'code', 'name_invalid'),
                          'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
