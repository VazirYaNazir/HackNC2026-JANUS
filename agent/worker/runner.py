"""One-request Linux container worker; never use this script as a host sandbox.

The host owns a dedicated, constrained container for each request and confirms
its removal before accepting the result. Only source/artifact bytes cross the
boundary. This supervisor additionally checks its boundary and owns all children.
"""
import base64
import binascii
import ctypes
import errno
import json
import math
import os
from pathlib import Path
import platform
import resource
import selectors
import signal
import socket
import struct
import subprocess
import sys
import time


WORK = Path("/work")
CGROUP = Path("/sys/fs/cgroup")
MAX_SOURCE_BYTES = 128 * 1024
MAX_STDIN_BYTES = 65536
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024
LIMIT_FIELDS = frozenset({"compile_seconds", "run_seconds", "diagnostics_bytes", "stdout_bytes", "stderr_bytes", "memory_bytes", "disk_bytes", "processes", "cpu_seconds"})
GUARANTEES = ["filesystem", "credentials", "network", "cpu", "memory", "processes", "disk", "timeout", "output"]
_CANCELLED = False


def parse_request(payload):
    if not isinstance(payload, dict):
        raise ValueError("Request must be an object")
    phase = payload.get("phase")
    keys = {"probe": {"phase", "limits"}, "compile": {"phase", "source", "link_args", "limits"}, "run": {"phase", "artifact_base64", "stdin", "capabilities", "limits"}}
    if not isinstance(phase, str) or phase not in keys or set(payload) != keys[phase]:
        raise ValueError("Unknown phase or request fields")
    limits = payload["limits"]
    if not isinstance(limits, dict) or set(limits) != LIMIT_FIELDS:
        raise ValueError("Complete resource limits are required")
    for name, value in limits.items():
        if name in {"compile_seconds", "run_seconds"}:
            maximum = 30 if name == "compile_seconds" else 3
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError("Invalid deadline")
        elif type(value) is not int or value <= 0 or value > (1 << 63) - 1:
            raise ValueError("Invalid resource budget")
    if max(limits["stdout_bytes"], limits["stderr_bytes"], limits["diagnostics_bytes"]) > MAX_ARTIFACT_BYTES:
        raise ValueError("Output budget exceeds worker maximum")
    if phase == "compile":
        source = payload["source"]
        if not isinstance(source, str) or not source.strip() or "\0" in source or len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
            raise ValueError("Invalid or oversized C source")
        if payload["link_args"] != []:
            raise ValueError("Only portable C without external dependencies is supported")
    elif phase == "run":
        if not isinstance(payload["stdin"], str) or len(payload["stdin"].encode("utf-8")) > MAX_STDIN_BYTES:
            raise ValueError("Invalid or oversized standard input")
        capabilities = payload["capabilities"]
        if not isinstance(capabilities, list) or any(not isinstance(capability, str) or capability not in {"files.read", "files.write", "process.spawn"} for capability in capabilities) or len(set(capabilities)) != len(capabilities):
            raise ValueError("Unsupported worker capability")
        validate_artifact(payload["artifact_base64"], limits["disk_bytes"])
    return payload


def validate_artifact(encoded, max_bytes):
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("Invalid artifact budget")
    budget = min(max_bytes, MAX_ARTIFACT_BYTES)
    if not isinstance(encoded, str) or len(encoded) > 4 * ((budget + 2) // 3) or not encoded.isascii():
        raise ValueError("Invalid or oversized artifact")
    try:
        artifact = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Invalid artifact encoding") from None
    if not 64 <= len(artifact) <= budget or artifact[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("Artifact must be a Linux ELF64 executable")
    elf_type, machine, version = struct.unpack_from("<HHI", artifact, 16)
    expected = {"x86_64": 62, "aarch64": 183, "arm64": 183}.get(platform.machine().lower())
    if expected is None or machine != expected or elf_type not in {2, 3} or version != 1 or struct.unpack_from("<H", artifact, 52)[0] != 64:
        raise ValueError("Artifact architecture or ELF header is invalid")
    # Linux accepts SYSV (0) and GNU/Linux (3) ELF ABI identifiers.
    if artifact[7] not in {0, 3}:
        raise ValueError("Artifact must target Linux")
    return artifact


def _counters(path):
    result = {}
    for line in path.read_text(encoding="ascii").splitlines():
        name, value = line.split()
        result[name] = int(value)
    return result


class CgroupMonitor:
    def __init__(self, root, limits, period):
        self.root, self.limits, self.period = root, limits, period
        self.oom = _counters(root / "memory.events")["oom_kill"]
        self.pids = _counters(root / "pids.events")["max"]

    def usage(self):
        return _counters(self.root / "cpu.stat")["usage_usec"] / 1_000_000

    def exceeded(self):
        return (self.usage() >= self.limits["cpu_seconds"] - self.period
                or _counters(self.root / "memory.events")["oom_kill"] > self.oom
                or _counters(self.root / "pids.events")["max"] > self.pids)

    def wall_allowance(self):
        # CFS may spend a quota immediately at a period boundary. Reserve two
        # periods as well as cleanup time; one CPU cannot spend the remaining
        # aggregate budget before this conservative wall deadline.
        return self.limits["cpu_seconds"] - self.usage() - 2 * self.period


def assert_cgroup_limits(limits, root=CGROUP):
    try:
        memory = int((root / "memory.max").read_text().strip())
        swap = int((root / "memory.swap.max").read_text().strip())
        pids = int((root / "pids.max").read_text().strip())
        quota, period = map(int, (root / "cpu.max").read_text().split())
        burst = root / "cpu.max.burst"
        if not (0 < memory <= limits["memory_bytes"] and swap == 0 and 0 < pids <= limits["processes"] + 1
                and 0 < quota <= period <= 100000 and (not burst.exists() or int(burst.read_text()) == 0)):
            raise ValueError("Container resource enforcement does not match the request")
        monitor = CgroupMonitor(root, limits, period / 1_000_000)
        if monitor.wall_allowance() <= .2:
            raise ValueError("Insufficient aggregate CPU allowance")
        return monitor
    except (OSError, KeyError, TypeError, ValueError):
        raise ValueError("Verified cgroup v2 resource enforcement is required") from None


def _mounts():
    result = {}
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        fields = before.split()
        result[fields[4]] = (set(fields[5].split(",")), after.split()[0])
    return result


def _network_interfaces():
    """Query the kernel's current network namespace before installing seccomp.

    Linux libc uses a temporary NETLINK_ROUTE socket for if_nameindex and
    closes it before returning. No socket is inherited by generated programs.
    Unlike proc/sysfs mounts, this query follows the caller's actual namespace.
    """
    entries = socket.if_nameindex()
    if not isinstance(entries, list) or not entries:
        raise ValueError("Verified kernel network namespace inspection is required")
    indexes, interfaces = set(), set()
    for entry in entries:
        if not isinstance(entry, tuple) or len(entry) != 2:
            raise ValueError("Verified kernel network namespace inspection is required")
        index, name = entry
        if (type(index) is not int or index <= 0 or not isinstance(name, str) or not name
                or len(name.split()) != 1 or index in indexes or name in interfaces):
            raise ValueError("Verified kernel network namespace inspection is required")
        indexes.add(index)
        interfaces.add(name)
    return interfaces


def _proc_network_interfaces(path=Path("/proc/self/net/dev")):
    """Supplemental proc view for diagnostics, never the isolation authority."""
    lines = path.read_text(encoding="ascii").splitlines()
    if len(lines) < 3 or [part.strip() for part in lines[0].split("|")] != ["Inter-", "Receive", "Transmit"]:
        raise ValueError("Verified network namespace inspection is required")
    headers = [part.split() for part in lines[1].split("|")]
    if headers != [["face"], ["bytes", "packets", "errs", "drop", "fifo", "frame", "compressed", "multicast"],
                   ["bytes", "packets", "errs", "drop", "fifo", "colls", "carrier", "compressed"]]:
        raise ValueError("Verified network namespace inspection is required")
    interfaces = set()
    for line in lines[2:]:
        name, values = line.rsplit(":", 1)
        name, counters = name.strip(), values.split()
        if (not name or len(name.split()) != 1 or name in interfaces
                or len(counters) != 16 or not all(value.isdecimal() for value in counters)):
            raise ValueError("Verified network namespace inspection is required")
        interfaces.add(name)
    return interfaces


def assert_runtime(limits):
    if sys.platform != "linux" or os.getpid() != 1 or os.getuid() == 0:
        raise ValueError("Worker must be the unprivileged init process of its own Linux container")
    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
    if int(status.get("CapEff", "1").strip(), 16) or int(status.get("CapBnd", "1").strip(), 16) or status.get("NoNewPrivs", "0").strip() != "1":
        raise ValueError("Dropped capabilities and no-new-privileges are required")
    mounts = _mounts()
    if "/" not in mounts or "ro" not in mounts["/"][0] or "/work" not in mounts or mounts["/work"][1] != "tmpfs" or "noexec" in mounts["/work"][0]:
        raise ValueError("Read-only root and bounded scratch tmpfs are required")
    work = os.statvfs(WORK)
    scratch_bytes = work.f_blocks * work.f_frsize
    if os.access("/dev/shm", os.W_OK):
        shm = os.statvfs("/dev/shm")
        scratch_bytes += shm.f_blocks * shm.f_frsize
    if scratch_bytes > limits["disk_bytes"] or not os.access(WORK, os.W_OK):
        raise ValueError("Writable storage exceeds the disk budget")
    if _network_interfaces() != {"lo"}:
        raise ValueError("Worker networking must be disabled")
    if set(os.environ) - {"PATH", "HOSTNAME", "HOME", "LANG", "LC_ALL", "TERM"}:
        raise ValueError("Worker environment must not contain host credentials")
    return assert_cgroup_limits(limits)


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    return libc


def _prctl(option, value):
    if _libc().prctl(option, value, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "Required process restriction is unavailable")


def install_seccomp(allow_spawn=False, allow_write=False):
    """No host APIs, sockets, introspection of the supervisor or ungranted forks."""
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint]
    library.seccomp_rule_add.restype = ctypes.c_int
    class Comparison(ctypes.Structure):
        _fields_ = [("argument", ctypes.c_uint), ("operator", ctypes.c_int), ("mask", ctypes.c_uint64), ("value", ctypes.c_uint64)]
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int, ctypes.c_uint, ctypes.POINTER(Comparison)]
    library.seccomp_rule_add_array.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise OSError("Required seccomp filtering is unavailable")
    denied = ["socket", "socketpair", "ptrace", "process_vm_readv", "process_vm_writev", "pidfd_getfd", "kcmp", "bpf", "perf_event_open", "keyctl", "add_key", "request_key", "mount", "umount2", "unshare", "setns", "userfaultfd", "io_uring_setup", "io_uring_register", "io_uring_enter", "memfd_create", "openat2"]
    if not allow_write:
        denied += ["creat", "mkdir", "mkdirat", "rename", "renameat", "renameat2", "unlink", "unlinkat", "rmdir", "truncate", "ftruncate", "fallocate", "link", "linkat", "symlink", "symlinkat", "mknod", "mknodat", "chmod", "fchmod", "fchmodat", "fchmodat2", "chown", "lchown", "fchown", "fchownat", "setxattr", "lsetxattr", "fsetxattr", "removexattr", "lremovexattr", "fremovexattr", "utime", "utimes", "utimensat"]
    if not allow_spawn:
        denied += ["clone", "clone3", "fork", "vfork"]
    try:
        for name in denied:
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number >= 0 and library.seccomp_rule_add(context, 0x00050000 | errno.EPERM, number, 0) != 0:
                raise OSError("Required seccomp rule is unavailable")
        for name, argument in (() if allow_write else (("open", 1), ("openat", 2))):
            number = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if number < 0:
                continue
            for bit in (os.O_WRONLY, os.O_RDWR, os.O_CREAT, os.O_TRUNC, os.O_APPEND, getattr(os, "O_TMPFILE", 0)):
                if not bit:
                    continue
                comparison = Comparison(argument, 7, bit, bit)  # SCMP_CMP_MASKED_EQ
                if library.seccomp_rule_add_array(context, 0x00050000 | errno.EPERM, number, 1, ctypes.byref(comparison)) != 0:
                    raise OSError("Required filesystem restriction is unavailable")
        _prctl(38, 1)  # PR_SET_NO_NEW_PRIVS
        if library.seccomp_load(context) != 0:
            raise OSError("Required seccomp filtering is unavailable")
    finally:
        library.seccomp_release(context)


def _child_setup(limits, restrict_processes, allow_write):
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits["disk_bytes"], limits["disk_bytes"]))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CPU, (limits["cpu_seconds"], limits["cpu_seconds"]))
    if restrict_processes is not None:
        install_seccomp(not restrict_processes, allow_write)


def _descendants():
    parent = os.getpid()
    relationships = {}
    for path in Path("/proc").iterdir():
        if path.name.isdecimal():
            try:
                fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
                relationships[int(path.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
    owned = {parent}
    while True:
        found = {pid for pid, ppid in relationships.items() if ppid in owned}
        if found <= owned:
            return owned - {parent}
        owned |= found


def _cleanup(process, deadline):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    while True:
        for pid in _descendants():
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=max(.001, min(.02, deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            pass
        if process.returncode is not None:
            while True:
                try:
                    pid, _ = os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    break
                if pid == 0:
                    break
        if not _descendants():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(.002)


def _text(buffer, limit):
    # Replacement characters can expand malformed UTF-8. Cap the final text's
    # UTF-8 representation as well as the raw pipe bytes.
    return bytes(buffer).decode("utf-8", "replace").encode("utf-8")[:limit].decode("utf-8", "ignore")


def supervise(command, *, cwd, stdin, timeout, stdout_limit, stderr_limit, limits, monitor=None, restrict_processes=None, allow_write=False):
    started = time.monotonic()
    _prctl(36, 1)  # PR_SET_CHILD_SUBREAPER, including helper tests outside PID1.
    _prctl(4, 0)   # PR_SET_DUMPABLE: untrusted children cannot inspect the supervisor.
    allowance = min(timeout, monitor.wall_allowance()) if monitor else timeout
    deadline = started + allowance
    active_deadline = max(started, deadline - min(.15, allowance / 3))
    result = {"returncode": None, "stdout": "", "stderr": "", "elapsed_ms": 0, "timed_out": False, "output_limited": False, "unavailable": False, "cancelled": False, "resource_limited": False}
    buffers = [bytearray(), bytearray()]
    limits_out = [stdout_limit, stderr_limit]
    selector = selectors.DefaultSelector()
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True, close_fds=True, env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "TMPDIR": str(cwd), "LANG": "C.UTF-8"},
                                   preexec_fn=lambda: _child_setup(limits, restrict_processes, allow_write))
        for index, pipe in enumerate((process.stdout, process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ, index)
        input_bytes = stdin.encode("utf-8")
        offset = 0
        if input_bytes:
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, 2)
        else:
            process.stdin.close()
        cleaned = False
        while selector.get_map() or process.poll() is None:
            if _CANCELLED:
                result["cancelled"] = True
                break
            if time.monotonic() >= active_deadline:
                result["resource_limited" if monitor and allowance < timeout else "timed_out"] = True
                break
            if monitor and monitor.exceeded():
                result["resource_limited"] = True
                break
            if process.poll() is not None and not cleaned:
                cleaned = _cleanup(process, deadline)
                if not cleaned:
                    result["unavailable"] = True
                    break
            for key, _ in selector.select(min(.005, max(0, active_deadline - time.monotonic()))):
                pipe, index = key.fileobj, key.data
                if index == 2:
                    try:
                        offset += os.write(pipe.fileno(), input_bytes[offset:offset + 4096])
                    except BrokenPipeError:
                        offset = len(input_bytes)
                    if offset == len(input_bytes):
                        selector.unregister(pipe)
                        pipe.close()
                    continue
                data = os.read(pipe.fileno(), 4096)
                if not data:
                    selector.unregister(pipe)
                    pipe.close()
                    continue
                remaining = limits_out[index] - len(buffers[index])
                buffers[index].extend(data[:remaining])
                if len(data) > remaining:
                    result["output_limited"] = True
                    break
            if result["output_limited"]:
                break
    except (OSError, subprocess.SubprocessError, ValueError, KeyError):
        result["unavailable"] = True
    finally:
        if process is not None:
            if not _cleanup(process, deadline):
                result["unavailable"] = True
            result["returncode"] = process.returncode
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()
        selector.close()
    if monitor is not None:
        try:
            if monitor.exceeded():
                result["resource_limited"] = True
        except (OSError, ValueError, KeyError):
            result["unavailable"] = True
    result["stdout"], result["stderr"] = (_text(buffer, limit) for buffer, limit in zip(buffers, limits_out))
    result["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return result


def handle_request(payload):
    request = parse_request(payload)
    limits = request["limits"]
    monitor = assert_runtime(limits)
    if request["phase"] == "probe":
        install_seccomp(False)
        architecture = {"x86_64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
        if architecture is None or not Path("/usr/bin/gcc").is_file():
            raise ValueError("Portable C compiler or architecture is unavailable")
        return {"ready": True, "architecture": architecture, "guarantees": GUARANTEES}
    if request["phase"] == "compile":
        source, executable = WORK / "program.c", WORK / "program"
        source.write_text(request["source"], encoding="utf-8")
        result = supervise(["/usr/bin/gcc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror=implicit-function-declaration", str(source), "-o", str(executable), "-lm"],
                           cwd=WORK, stdin="", timeout=limits["compile_seconds"], stdout_limit=limits["diagnostics_bytes"], stderr_limit=limits["diagnostics_bytes"], limits=limits, monitor=monitor)
        result["artifact_base64"] = None
        if result["returncode"] == 0 and not any(result[name] for name in ("timed_out", "output_limited", "unavailable", "cancelled", "resource_limited")):
            if executable.is_symlink() or not executable.is_file() or executable.stat().st_size > min(limits["disk_bytes"], MAX_ARTIFACT_BYTES):
                raise ValueError("Compiler artifact is invalid or oversized")
            encoded = base64.b64encode(executable.read_bytes()).decode("ascii")
            validate_artifact(encoded, limits["disk_bytes"])
            result["artifact_base64"] = encoded
        return result
    executable = WORK / "program"
    executable.write_bytes(validate_artifact(request["artifact_base64"], limits["disk_bytes"]))
    executable.chmod(0o500)
    return supervise([str(executable)], cwd=WORK, stdin=request["stdin"], timeout=limits["run_seconds"], stdout_limit=limits["stdout_bytes"], stderr_limit=limits["stderr_bytes"], limits=limits, monitor=monitor,
                     restrict_processes="process.spawn" not in request["capabilities"], allow_write="files.write" in request["capabilities"])


def _read_request():
    data = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin.buffer, selectors.EVENT_READ)
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline and not _CANCELLED:
            if not selector.select(min(.1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(sys.stdin.fileno(), 65536)
            if not chunk:
                return json.loads(data)
            if len(data) + len(chunk) > MAX_REQUEST_BYTES:
                raise ValueError("Worker request is oversized")
            data.extend(chunk)
    finally:
        selector.close()
    raise ValueError("Worker input deadline exceeded")


def main():
    def cancel(_signum, _frame):
        global _CANCELLED
        _CANCELLED = True
    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    try:
        result = handle_request(_read_request())
    except (OSError, ValueError, TypeError, KeyError, UnicodeError):
        result = {"returncode": None, "stdout": "", "stderr": "Worker boundary unavailable or invalid request", "elapsed_ms": 0, "timed_out": False, "output_limited": False, "unavailable": True, "cancelled": _CANCELLED}
    print(json.dumps(result, ensure_ascii=True, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
