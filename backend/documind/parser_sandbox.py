"""Restrict the PDF subprocess before it opens the input file."""

import ctypes as c
import errno
import json
import os
import platform
import resource
import stat
import sys
import sysconfig
from pathlib import Path

MIB = 1024 * 1024
RESULT_LIMIT = 64 * MIB
MEMORY_LIMIT = 1536 * MIB


def readable_paths(config):
    paths = [
        Path(__file__).parent,
        Path(sysconfig.get_path("stdlib")),
        Path(sysconfig.get_path("platlib")),
        Path(sys.base_prefix) / "lib",
        *(
            Path(name)
            for name in (
                "/lib",
                "/lib64",
                "/usr/lib",
                "/System/Library",
                "/usr/share",
                "/etc/ld.so.cache",
                "/proc/cpuinfo",
                "/proc/meminfo",
                "/sys/devices/system/cpu",
                "/dev/null",
                "/dev/urandom",
                "/dev/random",
            )
        ),
    ]
    if config.get("ocr_enabled"):
        paths.append(Path(config["ocr_model_root"]))
    return sorted({path.resolve() for path in paths if path.exists()})


def restrict(folder, config):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    file_limit = max(RESULT_LIMIT, config["max_rendered_bytes"])
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))
    paths = readable_paths(config)
    if sys.platform == "darwin":
        macos(folder, paths)
    elif sys.platform == "linux":
        # Linux native allocations are bounded before any PDF decoding begins.
        # OCR also reserves address space for BLAS/OpenMP arenas.
        address_space = (6 * 1024 * MIB) if config.get("ocr_enabled") else MEMORY_LIMIT
        resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
        linux(folder, paths)
    else:
        raise OSError("Unsupported PDF sandbox platform")


def macos(folder, paths):
    def location(path):
        return f"({'subpath' if path.is_dir() else 'literal'} {json.dumps(str(path))})"

    sandbox = c.CDLL("/usr/lib/libsandbox.dylib")
    sandbox.sandbox_init.argtypes = (c.c_char_p, c.c_uint64, c.c_void_p)
    profile = "\n".join(
        (
            "(version 1)",
            "(deny default)",
            "(allow sysctl-read)",
            "(allow file-read-metadata)",
            "(allow file-read-data " + " ".join(map(location, [folder, *paths])) + ")",
            "(allow file-write* " + location(folder) + ' (literal "/dev/null"))',
        )
    )
    if sandbox.sandbox_init(profile.encode(), 0, None):
        raise OSError("Unable to apply macOS PDF sandbox")


def linux(folder, paths):
    if platform.machine() not in {"x86_64", "aarch64"}:
        raise OSError("Unsupported Linux PDF sandbox architecture")
    libc = c.CDLL(None, use_errno=True)
    libc.syscall.restype = c.c_long
    seccomp = c.CDLL("libseccomp.so.2")

    def checked(value):
        if value < 0:
            raise OSError(c.get_errno(), "Unable to apply Linux PDF sandbox")
        return value

    # Landlock ABI 3 adds truncate restrictions. These syscall numbers are
    # shared by the two supported Linux architectures.
    create, add, restrict_self = 444, 445, 446
    if checked(libc.syscall(create, None, 0, 1)) < 3:
        raise OSError("Linux PDF sandbox requires Landlock ABI 3")
    access = c.c_uint64((1 << 15) - 1)

    class PathRule(c.Structure):
        _pack_ = 1
        _fields_ = [("allowed_access", c.c_uint64), ("parent_fd", c.c_int32)]

    fd = checked(libc.syscall(create, c.byref(access), c.sizeof(access), 0))
    try:
        for path in [*paths, folder]:
            # Execute is disabled after the interpreter starts. Only the private
            # work directory can create or alter files.
            permissions = 1 << 2
            if path == folder:
                permissions |= sum(1 << bit for bit in (1, 3, 4, 5, 7, 8, 13, 14))
            if path != folder and path.is_dir():
                permissions |= 1 << 3
            if path == Path("/dev/null"):
                permissions |= 1 << 1
            parent = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathRule(permissions, parent)
                checked(libc.syscall(add, fd, 1, c.byref(rule), 0))
            finally:
                os.close(parent)
        checked(libc.prctl(38, 1, 0, 0, 0))  # PR_SET_NO_NEW_PRIVS
        checked(libc.syscall(restrict_self, fd, 0))
    finally:
        os.close(fd)

    seccomp.seccomp_init.argtypes = (c.c_uint32,)
    seccomp.seccomp_init.restype = c.c_void_p
    seccomp.seccomp_rule_add.argtypes = (c.c_void_p, c.c_uint32, c.c_int, c.c_uint)
    seccomp.seccomp_syscall_resolve_name.argtypes = (c.c_char_p,)

    class Argument(c.Structure):
        _fields_ = [
            ("arg", c.c_uint),
            ("operation", c.c_int),
            ("mask", c.c_uint64),
            ("value", c.c_uint64),
        ]

    seccomp.seccomp_rule_add_array.argtypes = (
        c.c_void_p,
        c.c_uint32,
        c.c_int,
        c.c_uint,
        c.POINTER(Argument),
    )
    seccomp.seccomp_load.argtypes = (c.c_void_p,)
    seccomp.seccomp_release.argtypes = (c.c_void_p,)
    context = seccomp.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise OSError("Unable to initialize PDF syscall filter")
    try:
        denied = 0x00050000 | errno.EPERM  # SCMP_ACT_ERRNO
        for name in (
            "socket",
            "socketpair",
            "ptrace",
            "process_vm_readv",
            "process_vm_writev",
            "kill",
            "pidfd_getfd",
            "pidfd_send_signal",
            "io_uring_setup",
            "fork",
            "vfork",
            "setsid",
            "setpgid",
            "unshare",
        ):
            number = seccomp.seccomp_syscall_resolve_name(name.encode())
            if number >= 0 and seccomp.seccomp_rule_add(context, denied, number, 0) != 0:
                raise OSError("Unable to configure PDF syscall filter")
        # Keep pthreads, but never let the parser create an independent process.
        clone = seccomp.seccomp_syscall_resolve_name(b"clone")
        comparison = Argument(0, 7, 0x10000, 0)  # SCMP_CMP_MASKED_EQ, CLONE_THREAD
        if seccomp.seccomp_rule_add_array(context, denied, clone, 1, c.byref(comparison)) != 0:
            raise OSError("Unable to restrict PDF subprocess creation")
        clone3 = seccomp.seccomp_syscall_resolve_name(b"clone3")
        if clone3 >= 0 and seccomp.seccomp_rule_add(context, 0x00050000 | errno.ENOSYS, clone3, 0):
            raise OSError("Unable to restrict PDF subprocess creation")
        if seccomp.seccomp_load(context) != 0:
            raise OSError("Unable to load PDF syscall filter")
    finally:
        seccomp.seccomp_release(context)


def read_output(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Invalid PDF parser output")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Invalid PDF parser output")
        return data
