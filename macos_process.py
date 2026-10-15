"""Read-only Darwin process inspection using system facilities."""

import ctypes
import os
import subprocess
import sys


class _ProcBSDInfo(ctypes.Structure):
    # Public struct proc_bsdinfo from the macOS SDK's sys/proc_info.h.
    _fields_ = [
        ('pbi_flags', ctypes.c_uint32), ('pbi_status', ctypes.c_uint32),
        ('pbi_xstatus', ctypes.c_uint32), ('pbi_pid', ctypes.c_uint32),
        ('pbi_ppid', ctypes.c_uint32), ('pbi_uid', ctypes.c_uint32),
        ('pbi_gid', ctypes.c_uint32), ('pbi_ruid', ctypes.c_uint32),
        ('pbi_rgid', ctypes.c_uint32), ('pbi_svuid', ctypes.c_uint32),
        ('pbi_svgid', ctypes.c_uint32), ('rfu_1', ctypes.c_uint32),
        ('pbi_comm', ctypes.c_char * 16), ('pbi_name', ctypes.c_char * 32),
        ('pbi_nfiles', ctypes.c_uint32), ('pbi_pgid', ctypes.c_uint32),
        ('pbi_pjobc', ctypes.c_uint32), ('e_tdev', ctypes.c_uint32),
        ('e_tpgid', ctypes.c_uint32), ('pbi_nice', ctypes.c_int32),
        ('pbi_start_tvsec', ctypes.c_uint64), ('pbi_start_tvusec', ctypes.c_uint64),
    ]


def matches_identity(pid: int, identity: dict) -> bool:
    """Validate a daemon PID against its boot UUID and exact kernel birth time."""
    if pid <= 0 or not isinstance(identity, dict):
        return False
    try:
        seconds = int(identity['startSeconds'])
        microseconds = int(identity['startMicroseconds'])
        boot_id = identity['bootId']
        if seconds <= 0 or not 0 <= microseconds < 1_000_000 or not isinstance(boot_id, str) or not boot_id:
            return False
        inspect = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True).proc_pidinfo
        inspect.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        inspect.restype = ctypes.c_int
        info = _ProcBSDInfo()
        if inspect(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info):
            return False
        return (info.pbi_pid == pid and info.pbi_start_tvsec == seconds and
                info.pbi_start_tvusec == microseconds and
                query('/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid').decode().strip().lower() == boot_id.lower())
    except (OSError, AttributeError, ValueError, KeyError, TypeError, UnicodeError):
        return False


def query(*command: str) -> bytes:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                timeout=3, check=False)
        return result.stdout if result.returncode == 0 else b""
    except (OSError, subprocess.TimeoutExpired):
        return b""


def descendants(root: int) -> list[int]:
    children: dict[int, list[int]] = {}
    for line in query('/bin/ps', '-axo', 'pid=,ppid=').splitlines():
        try:
            pid, parent = map(int, line.split())
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
    pending, result, seen = [root], [], set()
    while pending:
        pid = pending.pop(0)
        if pid in seen:
            continue
        seen.add(pid)
        result.append(pid)
        pending.extend(children.get(pid, []))
    return result


def parse_procargs(data: bytes) -> tuple[list[str], dict[str, str]]:
    if len(data) < 4:
        return [], {}
    argc = int.from_bytes(data[:4], sys.byteorder, signed=True)
    if argc < 1 or argc > len(data):
        return [], {}
    executable_end = data.find(b'\0', 4)
    if executable_end < 0:
        return [], {}
    offset = executable_end + 1
    while offset < len(data) and data[offset] == 0:
        offset += 1
    args = []
    for _ in range(argc):
        end = data.find(b'\0', offset)
        if end < 0:
            return [], {}
        args.append(os.fsdecode(data[offset:end]))
        offset = end + 1
    environment = {}
    for item in data[offset:].split(b'\0'):
        key, separator, value = item.partition(b'=')
        if separator and key in (b'CODEX_HOME', b'CODEX_THREAD_ID', b'CLAUDE_CONFIG_DIR'):
            environment[os.fsdecode(key)] = os.fsdecode(value)
    return args, environment


def procargs(pid: int) -> tuple[list[str], dict[str, str]]:
    if pid <= 0:
        return [], {}
    try:
        libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
        sysctl = libc.sysctl
        sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        sysctl.restype = ctypes.c_int
        argmax = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        if sysctl((ctypes.c_int * 2)(1, 8), 2, ctypes.byref(argmax), ctypes.byref(size), None, 0):
            return [], {}
        if not 0 < argmax.value <= 16 * 1024 * 1024:
            return [], {}
        buffer = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(len(buffer))
        if sysctl((ctypes.c_int * 3)(1, 49, pid), 3, buffer, ctypes.byref(size), None, 0):
            return [], {}
        return parse_procargs(buffer.raw[:size.value])
    except (OSError, AttributeError):
        return [], {}


def open_paths(pid: int) -> list[str]:
    # NUL delimiters preserve spaces, Unicode, and newlines in filenames.
    output = query('/usr/sbin/lsof', '-a', '-p', str(pid), '-Fn0')
    return [os.fsdecode(field[1:]) for field in output.split(b'\0') if field.startswith(b'n')]
