"""Fixed, read-only guest diagnostics for the shipped worker image.

The host runs this source in a fresh container with the worker's unchanged
restrictions. Only fixed check names and booleans leave that container.
Optional interface observations contain bounded names and public flags only.
"""

DIAGNOSTIC_CHECKS = frozenset({
    "runtime", "seccomp", "compiler", "architecture", "memory", "swap",
    "processes", "cpu_quota", "cpu_period", "cpu_burst", "cpu_allowance",
    "container_init", "credentials", "readonly_root", "scratch", "network",
    "network_kernel_query", "network_proc_readable", "network_proc_loopback", "environment",
})
DIAGNOSTIC_CODES = frozenset({
    "WORKER_RUNTIME_UNAVAILABLE", "WORKER_SECCOMP_UNAVAILABLE",
    "WORKER_COMPILER_UNAVAILABLE", "WORKER_ARCHITECTURE_UNSUPPORTED",
    "WORKER_DIAGNOSTIC_UNAVAILABLE",
    "WORKER_NETWORK_UNAVAILABLE",
})

DIAGNOSTIC_SOURCE = r'''
import json
import os
from pathlib import Path
import platform
import runpy
import sys

names = ("runtime", "seccomp", "compiler", "architecture", "memory", "swap",
         "processes", "cpu_quota", "cpu_period", "cpu_burst", "cpu_allowance",
         "container_init", "credentials", "readonly_root", "scratch", "network",
         "network_kernel_query", "network_proc_readable", "network_proc_loopback", "environment")
checks = {name: False for name in names}
code = "WORKER_DIAGNOSTIC_UNAVAILABLE"
network_interfaces = []

def check(name, operation):
    try:
        checks[name] = bool(operation())
    except Exception:
        checks[name] = False

try:
    limits = json.loads(sys.stdin.read())
    worker = runpy.run_path("/opt/janus/runner.py")
    root = Path("/sys/fs/cgroup")
    number = lambda name: int((root / name).read_text().strip())
    check("memory", lambda: 0 < number("memory.max") <= limits["memory_bytes"])
    check("swap", lambda: number("memory.swap.max") == 0)
    check("processes", lambda: 0 < number("pids.max") <= limits["processes"] + 1)
    def cpu():
        return tuple(map(int, (root / "cpu.max").read_text().split()))
    check("cpu_quota", lambda: len(cpu()) == 2 and 0 < cpu()[0] <= cpu()[1])
    check("cpu_period", lambda: len(cpu()) == 2 and 0 < cpu()[1] <= 100000)
    check("cpu_burst", lambda: not (root / "cpu.max.burst").exists() or number("cpu.max.burst") == 0)
    check("cpu_allowance", lambda: worker["CgroupMonitor"](root, limits, cpu()[1] / 1000000).wall_allowance() > .2)
    check("container_init", lambda: sys.platform == "linux" and os.getpid() == 1 and os.getuid() != 0)
    def credentials():
        status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        return (int(status.get("CapEff", "1").strip(), 16) == 0
                and int(status.get("CapBnd", "1").strip(), 16) == 0
                and status.get("NoNewPrivs", "0").strip() == "1")
    check("credentials", credentials)
    check("readonly_root", lambda: "ro" in worker["_mounts"]()["/"][0])
    def scratch():
        mounts = worker["_mounts"]()
        options, kind = mounts["/work"]
        work = os.statvfs("/work")
        size = work.f_blocks * work.f_frsize
        if os.access("/dev/shm", os.W_OK):
            shm = os.statvfs("/dev/shm")
            size += shm.f_blocks * shm.f_frsize
        return kind == "tmpfs" and "noexec" not in options and size <= limits["disk_bytes"] and os.access("/work", os.W_OK)
    check("scratch", scratch)
    try:
        network_interfaces = worker["_network_interface_states"]()
        checks["network_kernel_query"] = True
        checks["network"] = worker["_network_isolated"](network_interfaces)
    except Exception:
        pass
    # /proc is supplemental evidence only. Docker Desktop can expose a view
    # that differs from the kernel query in this process's network namespace.
    try:
        proc_interfaces = worker["_proc_network_interfaces"]()
        checks["network_proc_readable"] = True
        checks["network_proc_loopback"] = proc_interfaces == {"lo"}
    except Exception:
        pass
    check("environment", lambda: not set(os.environ) - {"PATH", "HOSTNAME", "HOME", "LANG", "LC_ALL", "TERM"})
    try:
        worker["assert_runtime"](limits)
        checks["runtime"] = True
    except Exception:
        pass
    check("compiler", lambda: Path("/usr/bin/gcc").is_file())
    check("architecture", lambda: platform.machine().lower() in {"x86_64", "aarch64", "arm64"})
    # The real probe installs this identical filter in the trusted supervisor.
    # Never perform any further filesystem inspection after installation.
    try:
        worker["install_seccomp"](False)
        checks["seccomp"] = True
    except Exception:
        pass
    code = next((reason for name, reason in (
        ("network", "WORKER_NETWORK_UNAVAILABLE"),
        ("runtime", "WORKER_RUNTIME_UNAVAILABLE"),
        ("seccomp", "WORKER_SECCOMP_UNAVAILABLE"),
        ("compiler", "WORKER_COMPILER_UNAVAILABLE"),
        ("architecture", "WORKER_ARCHITECTURE_UNSUPPORTED"),
    ) if not checks[name]), None)
except Exception:
    pass
report = {"checks": checks, "code": code, "ready": code is None}
if network_interfaces:
    report["network_interfaces"] = network_interfaces
print(json.dumps(report, separators=(",", ":")), flush=True)
'''
