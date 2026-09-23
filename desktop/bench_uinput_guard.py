"""Block creation of kernel input devices before exec, without opening one.

This is a narrow Linux x86_64 seccomp restriction, not a general sandbox.
It is installed in a fresh single-threaded wrapper and inherited across exec.
"""
import ctypes
import errno
import os
import sys

# linux/uinput.h + asm-generic/ioctl.h on the explicitly supported x86_64 ABI.
UI_DEV_CREATE = 0x5501
SCMP_ARCH_X86_64 = 0xC000003E
SCMP_ACT_ALLOW = 0x7FFF0000
SCMP_ACT_ERRNO_EPERM = 0x00050000 | errno.EPERM
SCMP_CMP_MASKED_EQ = 7
SCMP_FLTATR_CTL_NNP = 3
READY_MARKER = ('CUA_UINPUT_GUARD_READY version=1 abi=linux-x86_64 '
                'ioctl=0x5501 mask=0xffffffff verified=1')


class GuardError(RuntimeError):
    pass


class ArgCompare(ctypes.Structure):
    _fields_ = [('arg', ctypes.c_uint), ('op', ctypes.c_int),
                ('datum_a', ctypes.c_uint64), ('datum_b', ctypes.c_uint64)]


def _load_seccomp():
    try:
        library = ctypes.CDLL('libseccomp.so.2', use_errno=True)
        declarations = {
            'seccomp_init': ([ctypes.c_uint32], ctypes.c_void_p),
            'seccomp_release': ([ctypes.c_void_p], None),
            'seccomp_arch_native': ([], ctypes.c_uint32),
            'seccomp_syscall_resolve_name': ([ctypes.c_char_p], ctypes.c_int),
            'seccomp_attr_set': ([ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32], ctypes.c_int),
            'seccomp_rule_add_array': ([ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                        ctypes.c_uint, ctypes.POINTER(ArgCompare)], ctypes.c_int),
            'seccomp_load': ([ctypes.c_void_p], ctypes.c_int),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result
        return library
    except (OSError, AttributeError) as exc:
        raise GuardError('libseccomp.so.2 ou API necessária indisponível') from exc


def _check(result, phase):
    if result != 0:
        raise GuardError(f'falha ao {phase} (código {result})')


def _verify_filter(ioctl_number):
    # Seccomp runs before fd validation. An invalid fd must now yield EPERM,
    # not EBADF. No device is opened; include high-bit aliases of unsigned cmd.
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    for command in (UI_DEV_CREATE, UI_DEV_CREATE | (1 << 32)):
        ctypes.set_errno(0)
        result = libc.syscall(ctypes.c_long(ioctl_number), ctypes.c_long(-1),
                              ctypes.c_ulonglong(command), ctypes.c_void_p())
        if result != -1 or ctypes.get_errno() != errno.EPERM:
            raise GuardError('autoverificação do bloqueio falhou; CUA não será executado')


def install_filter():
    if (sys.platform != 'linux' or os.uname().machine != 'x86_64'
            or sys.byteorder != 'little' or ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(ctypes.c_long) != 8):
        raise GuardError('ABI não validada; requerido Linux x86_64 de 64 bits')
    library = _load_seccomp()
    if library.seccomp_arch_native() != SCMP_ARCH_X86_64:
        raise GuardError('arquitetura nativa de seccomp não confirmada')
    ioctl_number = library.seccomp_syscall_resolve_name(b'ioctl')
    if ioctl_number != 16:
        raise GuardError('syscall ioctl não confirmado para x86_64')
    context = library.seccomp_init(SCMP_ACT_ALLOW)
    if not context:
        raise GuardError('não foi possível criar o filtro seccomp')
    try:
        _check(library.seccomp_attr_set(context, SCMP_FLTATR_CTL_NNP, 1), 'exigir no_new_privs')
        # ioctl(fd, unsigned int cmd, arg): upper 32 syscall-argument bits are
        # ignored by the kernel. Mask them so equivalent requests cannot pass.
        comparison = ArgCompare(1, SCMP_CMP_MASKED_EQ, 0xFFFFFFFF, UI_DEV_CREATE)
        _check(library.seccomp_rule_add_array(context, SCMP_ACT_ERRNO_EPERM,
                                              ioctl_number, 1, ctypes.byref(comparison)), 'adicionar a regra')
        _check(library.seccomp_load(context), 'carregar o filtro')
    finally:
        library.seccomp_release(context)  # Releases userspace state, not the loaded filter.
    _verify_filter(ioctl_number)


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if not arguments or not os.path.isabs(arguments[0]):
            raise GuardError('informe executável absoluto seguido de seus argumentos')
        install_filter()
        print(READY_MARKER, file=sys.stderr, flush=True)
        os.execv(arguments[0], arguments)
    except (GuardError, OSError) as exc:
        print(f'CUA_UINPUT_GUARD_FAILED: {exc}', file=sys.stderr)
        return 126
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
