"""Protocol validation and resource supervision for the Linux container worker."""
import base64
from contextlib import ExitStack
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import platform
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from unittest.mock import Mock, MagicMock

from dataclasses import asdict
from agent.isolation import ResourceLimits


WORKER_PATH = Path(__file__).resolve().parents[1] / "worker" / "runner.py"
LINUX = sys.platform.startswith("linux")
LIMITS = asdict(ResourceLimits())
MACHINE = 183 if platform.machine().lower() in {"aarch64", "arm64"} else 62


def load_runner():
    spec = importlib.util.spec_from_file_location("janus_worker_runner", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def elf64(*, machine=MACHINE, kind=2):
    """A minimal Linux ELF64 header and executable segment, never executed here."""
    ident = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, kind, machine, 1,
                         0x400000, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    segment = struct.pack("<IIQQQQQQ", 1, 5, 0, 0x400000, 0x400000,
                          120, 120, 4096)
    return header + segment


def assert_process_stopped(test, pid):
    """An orphan reaped by init or awaiting reaping cannot execute more code."""
    status = Path(f"/proc/{pid}/stat")
    deadline = time.monotonic() + 2
    while True:
        try:
            state = status.read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return
        if state == "Z":
            return
        test.assertLess(time.monotonic(), deadline, f"Child {pid} remains alive")
        time.sleep(0.01)


@unittest.skipUnless(LINUX, "The container worker runs on Linux, including Docker Desktop")
class RequestValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def compile_request(self, **changes):
        return {"phase": "compile", "source": "int main(void){return 0;}",
                "link_args": [], "limits": dict(LIMITS), **changes}

    def run_request(self, **changes):
        return {"phase": "run", "artifact_base64": base64.b64encode(elf64()).decode(),
                "stdin": "", "capabilities": [], "limits": dict(LIMITS), **changes}

    def test_valid_compile_and_run_preserve_request_values(self):
        for request in (self.compile_request(), self.run_request(stdin="2\n3\n"), {"phase": "probe", "limits": dict(LIMITS)}):
            with self.subTest(phase=request["phase"]):
                self.assertEqual(self.runner.parse_request(request), request)

    def test_nonobject_unknown_phase_and_injected_fields_are_rejected(self):
        cases = [None, [], "compile", {}, self.compile_request(phase="shell"),
                 self.compile_request(command=["/bin/sh"]), self.compile_request(env={"SECRET": "fixture"}),
                 self.run_request(source="extra"), self.compile_request(capabilities=["admin"])]
        for request in cases:
            with self.subTest(request=str(request)[:80]), self.assertRaises(ValueError):
                self.runner.parse_request(request)

    def test_missing_fields_cannot_enable_defaults_or_change_phase(self):
        for request in (self.compile_request(), self.run_request(), {"phase": "probe", "limits": dict(LIMITS)}):
            for key in request:
                incomplete = dict(request)
                del incomplete[key]
                with self.subTest(phase=request["phase"], missing=key), self.assertRaises(ValueError):
                    self.runner.parse_request(incomplete)

    def test_source_requires_bounded_nonempty_utf8_text_without_nul(self):
        for source in (None, 1, True, b"int main(){}", "", "int\0main(){}", "x" * 131073, "é" * 65537, "\ud800"):
            with self.subTest(source=str(source)[:30]), self.assertRaises(ValueError):
                self.runner.parse_request(self.compile_request(source=source))
        for source in ("x" * 131072, "é" * 65536):
            self.assertEqual(self.runner.parse_request(self.compile_request(source=source))["source"], source)

    def test_only_empty_link_arguments_are_supported(self):
        for value in (None, "", (), ["-lfoo"], ["-Xlinker", "--script=/private/file"], ["/tmp/lib.so"]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.runner.parse_request(self.compile_request(link_args=value))

    def test_stdin_is_text_with_utf8_byte_budget_but_can_include_nul(self):
        for stdin in (None, 0, b"data", "x" * 65537, "é" * 32769, "\ud800"):
            with self.subTest(stdin=str(stdin)[:30]), self.assertRaises(ValueError):
                self.runner.parse_request(self.run_request(stdin=stdin))
        for stdin in ("", "\0", "x" * 65536, "é" * 32768):
            self.assertEqual(self.runner.parse_request(self.run_request(stdin=stdin))["stdin"], stdin)

    def test_supported_capabilities_are_unique_and_scoped_to_the_container(self):
        for capabilities in ([], ["process.spawn"], ["files.read"], ["files.write"],
                             ["files.read", "files.write"], ["files.read", "files.write", "process.spawn"]):
            with self.subTest(capabilities=capabilities):
                self.assertEqual(self.runner.parse_request(self.run_request(capabilities=capabilities))["capabilities"], capabilities)
        for capabilities in (None, (), "process.spawn", ["network"], ["admin"], [True],
                             ["process.spawn", "process.spawn"], ["files.read", "files.read"],
                             ["files.write", "files.write"], ["files.read", "external.send"]):
            with self.subTest(capabilities=capabilities), self.assertRaises(ValueError):
                self.runner.parse_request(self.run_request(capabilities=capabilities))

    def test_limits_require_exact_fields_and_positive_numeric_values(self):
        for malformed in (None, [], {**LIMITS, "extra": 1}, {key: value for key, value in LIMITS.items() if key != "memory_bytes"}):
            with self.subTest(limits=malformed), self.assertRaises(ValueError):
                self.runner.parse_request(self.compile_request(limits=malformed))
        for key in LIMITS:
            for value in (None, "1", True, 0, -1):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.runner.parse_request(self.compile_request(limits={**LIMITS, key: value}))

    def test_integer_budgets_and_finite_deadlines_cannot_be_weakened(self):
        for key in set(LIMITS) - {"compile_seconds", "run_seconds"}:
            for value in (1.5, float("inf"), float("nan")):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.runner.parse_request(self.run_request(limits={**LIMITS, key: value}))
        for key, maximum in (("compile_seconds", 30), ("run_seconds", 3)):
            for value in (float("inf"), float("nan"), maximum + 0.001):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.runner.parse_request(self.compile_request(limits={**LIMITS, key: value}))


@unittest.skipUnless(LINUX, "Linux worker ELF format")
class ArtifactValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def validate(self, binary, budget=4096):
        return self.runner.validate_artifact(base64.b64encode(binary).decode(), budget)

    def test_host_elf_executables_and_pie_artifacts_are_accepted_at_exact_budget(self):
        for kind in (2, 3):
            artifact = elf64(kind=kind)
            self.assertEqual(self.validate(artifact, len(artifact)), artifact)

    def test_strict_base64_rejects_wrong_types_whitespace_alphabet_and_padding(self):
        valid = base64.b64encode(elf64()).decode()
        for encoded in (None, 1, valid.encode(), "", "%%%", valid + "\n", valid[:-1], "é", valid[:5] + " " + valid[5:]):
            with self.subTest(encoded=str(encoded)[:30]), self.assertRaises(ValueError):
                self.runner.validate_artifact(encoded, 4096)

    def test_encoded_budget_is_checked_before_allocating_decoded_artifact(self):
        oversized = base64.b64encode(elf64() + b"x").decode()
        with patch.object(self.runner.base64, "b64decode") as decoder, self.assertRaises(ValueError):
            self.runner.validate_artifact(oversized, len(elf64()))
        decoder.assert_not_called()

    def test_decoded_budget_is_checked_when_padding_has_same_encoded_length(self):
        artifact = elf64() + b"xy"
        with self.assertRaises(ValueError):
            self.validate(artifact, len(artifact) - 1)

    def test_scripts_windows_macho_and_truncated_elf_are_rejected(self):
        for artifact in (b"#!/bin/sh\necho unsafe\n", b"MZ" + b"\0" * 128,
                         b"\xcf\xfa\xed\xfe" + b"\0" * 128, elf64()[:63], b"\x7fELF"):
            with self.subTest(magic=artifact[:4]), self.assertRaises(ValueError):
                self.validate(artifact)

    def test_wrong_machine_object_file_endian_class_and_version_are_rejected(self):
        wrong_machine = 62 if MACHINE == 183 else 183
        artifacts = [elf64(machine=wrong_machine), elf64(kind=1), elf64(kind=4)]
        for offset, value in ((4, 1), (5, 2), (6, 0)):
            mutated = bytearray(elf64())
            mutated[offset] = value
            artifacts.append(bytes(mutated))
        for artifact in artifacts:
            with self.subTest(header=artifact[:24]), self.assertRaises(ValueError):
                self.validate(artifact)


@unittest.skipUnless(LINUX, "Linux cgroup resource enforcement")
class CgroupEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.values = {"memory.max": str(LIMITS["memory_bytes"]), "memory.swap.max": "0",
                       "pids.max": str(LIMITS["processes"] + 1), "cpu.max": "100000 100000",
                       "cpu.max.burst": "0", "cpu.stat": "usage_usec 0\n",
                       "memory.events": "oom_kill 0\n", "pids.events": "max 0\n"}
        for name, value in self.values.items():
            (self.root / name).write_text(value)

    def test_enforced_limits_return_monitor_and_allow_stricter_container_budgets(self):
        self.assertIsNotNone(self.runner.assert_cgroup_limits(LIMITS, self.root))
        (self.root / "memory.max").write_text(str(LIMITS["memory_bytes"] // 2))
        (self.root / "pids.max").write_text("2")
        (self.root / "cpu.max").write_text("50000 100000")
        self.assertIsNotNone(self.runner.assert_cgroup_limits(LIMITS, self.root))

    def test_missing_resource_evidence_fails_closed(self):
        for name in self.values.keys() - {"cpu.max.burst"}:
            path = self.root / name
            path.unlink()
            with self.subTest(file=name), self.assertRaises((ValueError, OSError)):
                self.runner.assert_cgroup_limits(LIMITS, self.root)
            path.write_text(self.values[name])

    def test_kernels_without_burst_support_can_enforce_the_cpu_quota(self):
        (self.root / "cpu.max.burst").unlink()
        self.assertIsNotNone(self.runner.assert_cgroup_limits(LIMITS, self.root))

    def test_unlimited_over_budget_and_malformed_memory_process_cpu_limits_fail(self):
        cases = {"memory.max": ("max", str(LIMITS["memory_bytes"] + 1), "not-a-number", "0", "-1"),
                 "memory.swap.max": ("max", "1", "-1"),
                 "pids.max": ("max", str(LIMITS["processes"] + 2), "0", "-1"),
                 "cpu.max": ("max 100000", "100001 100000", "100000 0", "0 100000", "-1 100000", "broken"),
                 "cpu.max.burst": ("1", "max", "-1")}
        for name, values in cases.items():
            for value in values:
                (self.root / name).write_text(value)
                with self.subTest(file=name, value=value), self.assertRaises((ValueError, OSError)):
                    self.runner.assert_cgroup_limits(LIMITS, self.root)
            (self.root / name).write_text(self.values[name])

    def test_prior_cpu_exhaustion_is_rejected_before_launch(self):
        (self.root / "cpu.stat").write_text(f"usage_usec {LIMITS['cpu_seconds'] * 1000000}\n")
        with self.assertRaises((ValueError, OSError)):
            self.runner.assert_cgroup_limits(LIMITS, self.root)

    def test_monitor_detects_cpu_budget_oom_kills_and_process_denials(self):
        for filename, value in (("cpu.stat", f"usage_usec {LIMITS['cpu_seconds'] * 1000000}\n"),
                                ("memory.events", "oom_kill 1\n"), ("pids.events", "max 1\n")):
            monitor = self.runner.assert_cgroup_limits(LIMITS, self.root)
            self.assertFalse(monitor.exceeded())
            (self.root / filename).write_text(value)
            with self.subTest(counter=filename):
                self.assertTrue(monitor.exceeded())
            (self.root / filename).write_text(self.values[filename])

    def test_wall_allowance_reserves_cfs_periods_and_spent_cpu(self):
        (self.root / "cpu.stat").write_text("usage_usec 500000\n")
        monitor = self.runner.assert_cgroup_limits(LIMITS, self.root)
        self.assertAlmostEqual(monitor.usage(), .5)
        self.assertAlmostEqual(monitor.wall_allowance(), LIMITS["cpu_seconds"] - .5 - .2)


@unittest.skipUnless(LINUX, "Linux container boundary inspection")
class RuntimeBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.pid = self.stack.enter_context(patch.object(self.runner.os, "getpid", return_value=1))
        self.uid = self.stack.enter_context(patch.object(self.runner.os, "getuid", return_value=1000))
        self.status = self.stack.enter_context(patch.object(Path, "read_text", return_value="CapEff:\t00000000\nCapBnd:\t00000000\nNoNewPrivs:\t1\n"))
        self.mounts = self.stack.enter_context(patch.object(self.runner, "_mounts", return_value={"/": ({"ro"}, "overlay"), "/work": ({"rw"}, "tmpfs")}))
        self.storage = self.stack.enter_context(patch.object(self.runner.os, "statvfs", return_value=SimpleNamespace(f_blocks=LIMITS["disk_bytes"] // 4096, f_frsize=4096)))
        self.access = self.stack.enter_context(patch.object(self.runner.os, "access", side_effect=lambda path, mode: str(path) != "/dev/shm"))
        self.interfaces = self.stack.enter_context(patch.object(self.runner, "_network_interface_states", return_value=[{"name": "lo", "loopback": True, "up": True}]))
        self.cgroups = self.stack.enter_context(patch.object(self.runner, "assert_cgroup_limits", return_value=Mock()))
        self.stack.enter_context(patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/work"}, clear=True))

    def assert_rejected(self):
        with self.assertRaises(ValueError):
            self.runner.assert_runtime(dict(LIMITS))
        self.cgroups.assert_not_called()

    def test_verified_container_reaches_cgroup_inspection(self):
        self.assertIs(self.runner.assert_runtime(dict(LIMITS)), self.cgroups.return_value)
        self.cgroups.assert_called_once_with(LIMITS)

    def test_host_process_or_root_user_cannot_claim_container_isolation(self):
        for pid, uid in ((42, 1000), (1, 0)):
            self.pid.return_value, self.uid.return_value = pid, uid
            with self.subTest(pid=pid, uid=uid):
                self.assert_rejected()

    def test_capabilities_and_privilege_escalation_are_always_denied(self):
        for status in ("CapEff: 1\nCapBnd: 0\nNoNewPrivs: 1\n",
                       "CapEff: 0\nCapBnd: 1\nNoNewPrivs: 1\n",
                       "CapEff: 0\nCapBnd: 0\nNoNewPrivs: 0\n",
                       "CapEff: 0\nNoNewPrivs: 1\n", ""):
            self.status.return_value = status
            with self.subTest(status=status):
                self.assert_rejected()

    def test_root_must_be_readonly_and_scratch_must_be_tmpfs(self):
        for mounts in ({}, {"/": ({"rw"}, "overlay"), "/work": ({"rw"}, "tmpfs")},
                       {"/": ({"ro"}, "overlay")},
                       {"/": ({"ro"}, "overlay"), "/work": ({"rw"}, "ext4")},
                       {"/": ({"ro"}, "overlay"), "/work": ({"rw", "noexec"}, "tmpfs")}):
            self.mounts.return_value = mounts
            with self.subTest(mounts=mounts):
                self.assert_rejected()

    def test_readonly_or_oversized_scratch_is_rejected(self):
        self.access.side_effect = lambda path, mode: False
        self.assert_rejected()
        self.access.side_effect = lambda path, mode: str(path) != "/dev/shm"
        self.storage.return_value = SimpleNamespace(f_blocks=LIMITS["disk_bytes"] // 4096 + 1, f_frsize=4096)
        self.assert_rejected()

    def test_writable_shared_memory_counts_toward_total_disk_budget(self):
        self.access.side_effect = lambda path, mode: True
        self.assert_rejected()

    def test_host_vm_sysfs_interfaces_do_not_override_the_process_network_namespace(self):
        # Docker Desktop can expose VM interfaces through its shared sysfs mount.
        # Namespace-local evidence still reports only loopback for network=none.
        with patch.object(Path, "iterdir", side_effect=AssertionError("sysfs must not be used for networking")):
            self.assertIs(self.runner.assert_runtime(dict(LIMITS)), self.cgroups.return_value)
        self.interfaces.assert_called_once_with()

    def test_missing_or_unverifiable_process_namespace_network_evidence_fails_closed(self):
        self.interfaces.side_effect = ValueError("Invalid kernel network evidence")
        self.assert_rejected()

    def test_dormant_linuxkit_tunnels_do_not_enable_worker_networking(self):
        for name in ("ip6tnl0", "tunl0", "sit0"):
            self.interfaces.return_value = [{"name": "lo", "loopback": True, "up": True},
                                           {"name": name, "loopback": False, "up": False}]
            with self.subTest(interface=name):
                self.assertIs(self.runner.assert_runtime(dict(LIMITS)), self.cgroups.return_value)

    def test_active_tunnels_or_missing_genuine_loopback_fail_closed(self):
        cases = ([{"name": "lo", "loopback": True, "up": True}, {"name": "ip6tnl0", "loopback": False, "up": True}],
                 [{"name": "eth0", "loopback": False, "up": False}],
                 [{"name": "lo", "loopback": False, "up": True}])
        for interfaces in cases:
            self.interfaces.return_value = interfaces
            with self.subTest(interfaces=interfaces):
                self.assert_rejected()

    def test_extra_network_interfaces_and_host_environment_are_rejected(self):
        self.interfaces.return_value = [{"name": "lo", "loopback": True, "up": True}, {"name": "eth0", "loopback": False, "up": True}]
        self.assert_rejected()
        self.interfaces.return_value = [{"name": "lo", "loopback": True, "up": True}]
        with patch.dict(os.environ, {"GEMINI_API_KEY": "private-key"}):
            self.assert_rejected()


@unittest.skipUnless(LINUX, "Linux kernel network namespace inspection")
class KernelNetworkNamespaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def test_loopback_comes_from_the_kernel_without_proc_or_sysfs_reads(self):
        with patch.object(self.runner.socket, "if_nameindex", return_value=[(1, "lo")]) as query, \
                patch.object(Path, "read_text", side_effect=AssertionError("proc must not be used")), \
                patch.object(Path, "iterdir", side_effect=AssertionError("sysfs must not be used")):
            self.assertEqual(self.runner._network_interfaces(), {"lo"})
        query.assert_called_once_with()

    def test_interface_indices_are_validated_but_need_not_be_contiguous(self):
        with patch.object(self.runner.socket, "if_nameindex", return_value=[(7, "lo")]):
            self.assertEqual(self.runner._network_interfaces(), {"lo"})

    def test_connected_namespaces_preserve_interfaces_for_runtime_rejection(self):
        for extra in ("eth0", "docker0", "veth123abc", "enp0s1"):
            with self.subTest(interface=extra), patch.object(self.runner.socket, "if_nameindex", return_value=[(1, "lo"), (3, extra)]):
                self.assertEqual(self.runner._network_interfaces(), {"lo", extra})

    def test_malformed_empty_or_duplicate_kernel_evidence_fails_closed(self):
        cases = (None, [], {}, "lo", [(1,)], [(1, "lo", "extra")], ["lo"],
                 [(True, "lo")], [(0, "lo")], [(-1, "lo")], [(1.0, "lo")], [("1", "lo")],
                 [(1, "")], [(1, None)], [(1, 5)],
                 [(1, "lo"), (1, "eth0")], [(1, "lo"), (2, "lo")])
        for values in cases:
            with self.subTest(values=values), patch.object(self.runner.socket, "if_nameindex", return_value=values), self.assertRaises(ValueError):
                self.runner._network_interfaces()

    def test_kernel_query_failure_cannot_claim_disabled_networking(self):
        with patch.object(self.runner.socket, "if_nameindex", side_effect=OSError("Kernel query unavailable")), self.assertRaises((OSError, ValueError)):
            self.runner._network_interfaces()


@unittest.skipUnless(LINUX, "Linux kernel interface flags inspection")
class NetworkInterfaceStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def socket_fixture(self):
        client = MagicMock()
        client.__enter__.return_value = client
        client.fileno.return_value = 11
        return client

    def test_trusted_kernel_flag_query_closes_the_socket_and_exposes_only_bool_state(self):
        client = self.socket_fixture()
        flags = {"lo": 0x1 | 0x8, "ip6tnl0": 0x80, "tunl0": 0x80}
        def ioctl(fd, request, buffer, *args):
            self.assertEqual(fd, 11)
            self.assertEqual(request, 0x8913)
            name = buffer[:16].split(b"\0", 1)[0]
            return struct.pack("16sH", name, flags[name.decode("ascii")]) + bytes(238)
        with patch.object(self.runner, "_network_interfaces", return_value=set(flags)), \
                patch.object(self.runner.socket, "socket", return_value=client) as opening, \
                patch.object(self.runner.fcntl, "ioctl", side_effect=ioctl) as query:
            states = self.runner._network_interface_states()
        opening.assert_called_once_with(self.runner.socket.AF_INET, self.runner.socket.SOCK_DGRAM)
        self.assertEqual(query.call_count, 3)
        self.assertGreaterEqual(client.__exit__.call_count + client.close.call_count, 1)
        self.assertCountEqual(states, [
            {"name": "lo", "loopback": True, "up": True},
            {"name": "ip6tnl0", "loopback": False, "up": False},
            {"name": "tunl0", "loopback": False, "up": False},
        ])
        self.assertTrue(all(type(state["up"]) is bool and type(state["loopback"]) is bool for state in states))

    def test_invalid_or_excessive_names_fail_before_opening_the_inspection_socket(self):
        cases = ({"x" * 16}, {"é"}, {"lo\0"}, {"lo space"},
                 {"v" + str(index) for index in range(33)})
        for names in cases:
            with self.subTest(names=names), patch.object(self.runner, "_network_interfaces", return_value=names), \
                    patch.object(self.runner.socket, "socket") as opening, self.assertRaises(ValueError):
                self.runner._network_interface_states()
            opening.assert_not_called()

    def test_failed_flag_query_fails_closed_and_closes_the_socket(self):
        client = self.socket_fixture()
        with patch.object(self.runner, "_network_interfaces", return_value={"lo"}), \
                patch.object(self.runner.socket, "socket", return_value=client), \
                patch.object(self.runner.fcntl, "ioctl", side_effect=OSError("Kernel flags unavailable")), \
                self.assertRaises((OSError, ValueError)):
            self.runner._network_interface_states()
        self.assertGreaterEqual(client.__exit__.call_count + client.close.call_count, 1)

    def test_unreadable_flag_response_cannot_claim_network_isolation(self):
        client = self.socket_fixture()
        with patch.object(self.runner, "_network_interfaces", return_value={"lo"}), \
                patch.object(self.runner.socket, "socket", return_value=client), \
                patch.object(self.runner.fcntl, "ioctl", return_value=b""), \
                self.assertRaises((OSError, ValueError, struct.error)):
            self.runner._network_interface_states()
        self.assertGreaterEqual(client.__exit__.call_count + client.close.call_count, 1)

    def test_isolation_allows_only_genuine_loopback_and_administratively_down_other_interfaces(self):
        loopback = {"name": "lo", "loopback": True, "up": True}
        for name in ("ip6tnl0", "tunl0", "sit0", "eth0"):
            with self.subTest(interface=name):
                self.assertTrue(self.runner._network_isolated([loopback, {"name": name, "loopback": False, "up": False}]))
                self.assertFalse(self.runner._network_isolated([loopback, {"name": name, "loopback": False, "up": True}]))
        self.assertFalse(self.runner._network_isolated([{"name": "lo", "loopback": False, "up": True}]))
        self.assertFalse(self.runner._network_isolated([{"name": "eth0", "loopback": False, "up": False}]))
        self.assertFalse(self.runner._network_isolated([]))


@unittest.skipUnless(LINUX, "Linux process network namespace inspection")
class NetworkNamespaceTests(unittest.TestCase):
    HEADER = (
        "Inter-|   Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "dev"

    def interfaces(self, content):
        self.path.write_text(content, encoding="ascii")
        return self.runner._proc_network_interfaces(self.path)

    def row(self, name="lo", counters=None):
        values = counters if counters is not None else [str(number) for number in range(16)]
        return f"  {name}: " + " ".join(values) + "\n"

    def test_disabled_network_reports_only_namespace_loopback(self):
        self.assertEqual(self.interfaces(self.HEADER + self.row()), {"lo"})

    def test_connected_namespace_preserves_all_interfaces_for_runtime_rejection(self):
        for extra in ("eth0", "docker0", "veth123abc", "enp0s1"):
            with self.subTest(interface=extra):
                self.assertEqual(self.interfaces(self.HEADER + self.row() + self.row(extra)), {"lo", extra})

    def test_namespace_inspection_never_queries_host_vm_sysfs(self):
        with patch.object(Path, "iterdir", side_effect=AssertionError("host sysfs is irrelevant")):
            self.assertEqual(self.interfaces(self.HEADER + self.row()), {"lo"})

    def test_missing_empty_malformed_or_incomplete_kernel_headers_fail_closed(self):
        cases = ("", self.row(), self.HEADER.splitlines()[0] + "\n" + self.row(),
                 self.HEADER.replace("Receive", "unknown") + self.row(),
                 self.HEADER.replace("carrier", "unknown") + self.row(), self.HEADER)
        for content in cases:
            with self.subTest(content=content[:40]), self.assertRaises(ValueError):
                self.interfaces(content)

    def test_wrong_counter_count_or_nondecimal_negative_values_fail_closed(self):
        cases = (["0"] * 15, ["0"] * 17,
                 ["-1"] + ["0"] * 15, ["1.0"] + ["0"] * 15,
                 ["not-a-number"] + ["0"] * 15)
        for counters in cases:
            with self.subTest(counters=counters), self.assertRaises(ValueError):
                self.interfaces(self.HEADER + self.row(counters=counters))

    def test_duplicate_empty_or_missing_interface_names_fail_closed(self):
        cases = (self.row() + self.row(), self.row(name=""), "  " + " ".join(["0"] * 16) + "\n")
        for content in cases:
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.interfaces(self.HEADER + content)


@unittest.skipUnless(LINUX, "Linux process tree supervision")
class SupervisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_python(self, source, *, stdin="", timeout=2, stdout_limit=4096, stderr_limit=4096):
        return self.runner.supervise([sys.executable, "-c", source], cwd=Path(self.tmp.name),
                                     stdin=stdin, timeout=timeout, stdout_limit=stdout_limit,
                                     stderr_limit=stderr_limit, limits=dict(LIMITS))

    def test_stdin_stdout_stderr_and_nonzero_exit_are_preserved(self):
        result = self.run_python("import sys;sys.stdout.write(sys.stdin.read());sys.stderr.write('diagnostic');sys.exit(7)", stdin="private fixture\n")
        self.assertEqual((result["returncode"], result["stdout"], result["stderr"]), (7, "private fixture\n", "diagnostic"))
        self.assertFalse(result["timed_out"] or result["output_limited"])
        self.assertGreaterEqual(result["elapsed_ms"], 0)

    def test_exact_output_budgets_are_not_overflow(self):
        result = self.run_python("import sys;sys.stdout.write('x'*1024);sys.stderr.write('y'*512)", stdout_limit=1024, stderr_limit=512)
        self.assertEqual((result["stdout"], result["stderr"]), ("x" * 1024, "y" * 512))
        self.assertFalse(result["output_limited"] or result["timed_out"])

    def test_stdout_and_stderr_overflow_are_independently_bounded(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                result = self.run_python(f"import sys;sys.{stream}.buffer.write(b'x'*200000)", stdout_limit=1024, stderr_limit=512)
                self.assertTrue(result["output_limited"])
                self.assertLessEqual(len(result["stdout"].encode()), 1024)
                self.assertLessEqual(len(result["stderr"].encode()), 512)
                self.assertFalse(result["timed_out"])

    def test_simultaneous_output_and_large_stdin_do_not_deadlock(self):
        source = "import sys,threading\nt=threading.Thread(target=lambda:sys.stdout.buffer.write(b'a'*200000));t.start();sys.stderr.buffer.write(b'b'*200000);t.join();sys.stdin.read()"
        result = self.run_python(source, stdin="x" * 65536, stdout_limit=1024, stderr_limit=512)
        self.assertTrue(result["output_limited"])
        self.assertFalse(result["timed_out"])
        self.assertLessEqual(len(result["stdout"].encode()), 1024)
        self.assertLessEqual(len(result["stderr"].encode()), 512)

    def test_multibyte_and_invalid_utf8_never_expand_output_budgets(self):
        for data in ("'é'.encode()*4096", "b'\\xff'*4096", "b'\\xe2\\x82' *4096"):
            with self.subTest(data=data):
                result = self.run_python(f"import sys;sys.stdout.buffer.write({data});sys.stderr.buffer.write({data})", stdout_limit=17, stderr_limit=13)
                self.assertTrue(result["output_limited"])
                self.assertLessEqual(len(result["stdout"].encode()), 17)
                self.assertLessEqual(len(result["stderr"].encode()), 13)

    def test_wall_timeout_kills_and_reaps_owned_process(self):
        processes = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            return process
        with patch.object(self.runner.subprocess, "Popen", side_effect=popen):
            result = self.run_python("import time;time.sleep(10)", timeout=0.05)
        self.assertTrue(result["timed_out"])
        self.assertLess(result["elapsed_ms"], 2000)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll())

    def test_exited_leader_cannot_leave_child_holding_output_pipes(self):
        source = "import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']);print(p.pid,flush=True)"
        result = self.run_python(source, timeout=0.3)
        self.assertEqual(result["returncode"], 0)
        self.assertLess(result["elapsed_ms"], 2000)
        assert_process_stopped(self, int(result["stdout"].strip()))

    def test_wall_timeout_cleans_descendants_and_not_just_the_leader(self):
        source = "import subprocess,sys,time\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(10)']);print(p.pid,flush=True);time.sleep(10)"
        result = self.run_python(source, timeout=0.3)
        self.assertTrue(result["timed_out"])
        assert_process_stopped(self, int(result["stdout"].strip()))

    def test_child_that_creates_a_new_session_is_still_owned_and_reaped(self):
        source = "import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import os,time;os.setsid();print(os.getpid(),flush=True);time.sleep(10)'],stdout=subprocess.PIPE,text=True);print(p.stdout.readline().strip(),flush=True)"
        result = self.run_python(source, timeout=.3)
        self.assertEqual(result["returncode"], 0)
        self.assertFalse(result["unavailable"])
        self.assertLess(result["elapsed_ms"], 2000)
        assert_process_stopped(self, int(result["stdout"].strip()))

    def test_worker_subprocess_gets_clean_environment_and_new_process_group(self):
        processes = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            processes.append(kwargs)
            return real_popen(*args, **kwargs)
        with patch.dict(os.environ, {"JANUS_TEST_SECRET": "never-forward"}), patch.object(self.runner.subprocess, "Popen", side_effect=popen):
            result = self.run_python("import os;print(os.environ.get('JANUS_TEST_SECRET','absent'))")
        self.assertEqual(result["stdout"], "absent\n")
        self.assertTrue(processes[0]["start_new_session"])
        self.assertNotIn("JANUS_TEST_SECRET", processes[0]["env"])

    def test_missing_executable_returns_a_bounded_failure_without_path_details(self):
        result = self.runner.supervise([str(Path(self.tmp.name) / "private-missing")], cwd=Path(self.tmp.name),
                                       stdin="", timeout=1, stdout_limit=1024, stderr_limit=512,
                                       limits=dict(LIMITS))
        self.assertIsNone(result["returncode"])
        self.assertNotIn(self.tmp.name, str(result))

    def test_aggregate_cgroup_budget_stops_the_worker_and_reaps_it(self):
        monitor = Mock()
        monitor.wall_allowance.return_value = 2
        monitor.exceeded.return_value = True
        result = self.runner.supervise([sys.executable, "-c", "import time;time.sleep(10)"],
                                       cwd=Path(self.tmp.name), stdin="", timeout=2,
                                       stdout_limit=1024, stderr_limit=512, limits=dict(LIMITS), monitor=monitor)
        self.assertTrue(result["resource_limited"])
        self.assertFalse(result["timed_out"])
        self.assertIsNotNone(result["returncode"])
        self.assertLess(result["elapsed_ms"], 2000)

    def test_lost_cgroup_evidence_stops_worker_and_hides_filesystem_errors(self):
        monitor = Mock()
        monitor.wall_allowance.return_value = 2
        monitor.exceeded.side_effect = OSError("private-cgroup-path")
        result = self.runner.supervise([sys.executable, "-c", "import time;time.sleep(10)"],
                                       cwd=Path(self.tmp.name), stdin="", timeout=2,
                                       stdout_limit=1024, stderr_limit=512, limits=dict(LIMITS), monitor=monitor)
        self.assertTrue(result["unavailable"])
        self.assertIsNotNone(result["returncode"])
        self.assertNotIn("private-cgroup-path", str(result))

    def run_with_final_evidence(self, *, evidence_lost=False):
        selector = self.runner.selectors.DefaultSelector()
        real_close = selector.close
        closed = False
        samples = []
        def close():
            nonlocal closed
            real_close()
            closed = True
        def exceeded():
            samples.append(closed)
            if closed and evidence_lost:
                raise OSError("private-final-cgroup-path")
            return closed
        monitor = Mock()
        monitor.wall_allowance.return_value = 2
        monitor.exceeded.side_effect = exceeded
        with patch.object(self.runner.selectors, "DefaultSelector", return_value=selector), patch.object(selector, "close", side_effect=close):
            result = self.runner.supervise([sys.executable, "-c", "import time;time.sleep(.02)"],
                                           cwd=Path(self.tmp.name), stdin="", timeout=2,
                                           stdout_limit=1024, stderr_limit=512, limits=dict(LIMITS), monitor=monitor)
        self.assertIn(False, samples, "The worker must check evidence while the child is running")
        self.assertEqual(samples[-1], True, "The final evidence sample must occur after pipe cleanup")
        return result

    def test_late_cgroup_event_is_detected_after_successful_child_exit(self):
        result = self.run_with_final_evidence()
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["resource_limited"])
        self.assertFalse(result["timed_out"] or result["unavailable"])

    def test_missing_final_cgroup_evidence_marks_unavailable_and_hides_details(self):
        result = self.run_with_final_evidence(evidence_lost=True)
        self.assertEqual(result["returncode"], 0)
        self.assertTrue(result["unavailable"])
        self.assertNotIn("private-final-cgroup-path", str(result))

    def test_local_cancellation_stops_worker_and_cleans_its_process_group(self):
        processes = []
        real_popen = subprocess.Popen
        def popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            processes.append(process)
            self.runner._CANCELLED = True
            return process
        with patch.object(self.runner, "_CANCELLED", False), patch.object(self.runner.subprocess, "Popen", side_effect=popen):
            result = self.run_python("import time;time.sleep(10)")
        self.assertTrue(result["cancelled"])
        self.assertFalse(result["timed_out"])
        self.assertIsNotNone(processes[0].poll())

    def test_seccomp_denies_network_and_ungranted_process_creation_in_the_child(self):
        try:
            ctypes.CDLL("libseccomp.so.2")
        except OSError:
            self.skipTest("libseccomp is installed in the worker image")
        source = "import socket,subprocess,sys\nfor action in (lambda:socket.socket(), lambda:subprocess.Popen([sys.executable,'-c','pass'])):\n try:\n  action();print('allowed')\n except OSError as error:\n  print('denied',error.errno)"
        result = self.runner.supervise([sys.executable, "-c", source], cwd=Path(self.tmp.name),
                                       stdin="", timeout=2, stdout_limit=1024, stderr_limit=512,
                                       limits=dict(LIMITS), restrict_processes=True)
        self.assertEqual((result["returncode"], result["stdout"]), (0, "denied 1\ndenied 1\n"))
        self.assertFalse(result["unavailable"])

    def test_explicit_process_capability_allows_children_while_network_stays_denied(self):
        try:
            ctypes.CDLL("libseccomp.so.2")
        except OSError:
            self.skipTest("libseccomp is installed in the worker image")
        source = "import socket,subprocess,sys\nsubprocess.run([sys.executable,'-c',\"print('child')\"],check=True)\ntry:\n socket.socket();print('network allowed')\nexcept OSError as error:\n print('network denied',error.errno)"
        result = self.runner.supervise([sys.executable, "-c", source], cwd=Path(self.tmp.name),
                                       stdin="", timeout=2, stdout_limit=1024, stderr_limit=512,
                                       limits=dict(LIMITS), restrict_processes=False)
        self.assertEqual((result["returncode"], result["stdout"]), (0, "child\nnetwork denied 1\n"))
        self.assertFalse(result["unavailable"])

    def test_seccomp_denies_filesystem_mutation_but_allows_fixture_reads_and_stdio(self):
        try:
            ctypes.CDLL("libseccomp.so.2")
        except OSError:
            self.skipTest("libseccomp is installed in the worker image")
        fixture = Path(self.tmp.name) / "public"
        fixture.write_text("fixture content")
        source = "import os\nprint(open('public').read())\nchecks=[('create',lambda:open('created','w')),('write',lambda:os.open('public',os.O_WRONLY)),('readwrite',lambda:os.open('public',os.O_RDWR)),('truncate',lambda:os.truncate('public',0)),('unlink',lambda:os.unlink('public')),('rename',lambda:os.rename('public','renamed')),('chmod',lambda:os.chmod('public',0o777)),('mkdir',lambda:os.mkdir('new')),('memfd',lambda:os.memfd_create('bypass'))]\nfor name,action in checks:\n try:\n  action();print(name,'allowed')\n except OSError as error:\n  print(name,'denied',error.errno)"
        result = self.runner.supervise([sys.executable, "-c", source], cwd=Path(self.tmp.name),
                                       stdin="", timeout=2, stdout_limit=2048, stderr_limit=512,
                                       limits=dict(LIMITS), restrict_processes=True)
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "fixture content\n" + "".join(f"{name} denied 1\n" for name in ("create", "write", "readwrite", "truncate", "unlink", "rename", "chmod", "mkdir", "memfd")))
        self.assertFalse(result["unavailable"])
        self.assertEqual(fixture.read_text(), "fixture content")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [fixture])

    def test_granted_scratch_writes_allow_create_read_write_rename_and_unlink_but_no_network(self):
        try:
            ctypes.CDLL("libseccomp.so.2")
        except OSError:
            self.skipTest("libseccomp is installed in the worker image")
        source = "import os,socket\nwith open('draft','w') as stream:\n stream.write('initial')\nwith open('draft','r+') as stream:\n print(stream.read());stream.seek(0);stream.write('changed')\nos.rename('draft','renamed');print(open('renamed').read());os.unlink('renamed');print('scratch empty',not os.listdir('.'))\ntry:\n socket.socket();print('network allowed')\nexcept OSError as error:\n print('network denied',error.errno)"
        result = self.runner.supervise([sys.executable, "-c", source], cwd=Path(self.tmp.name),
                                       stdin="", timeout=2, stdout_limit=2048, stderr_limit=512,
                                       limits=dict(LIMITS), restrict_processes=True, allow_write=True)
        self.assertEqual((result["returncode"], result["stdout"]), (0, "initial\nchanged\nscratch empty True\nnetwork denied 1\n"))
        self.assertFalse(result["unavailable"])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])


@unittest.skipUnless(LINUX, "Linux worker request dispatch")
class DispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runner = load_runner()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(self.runner, "WORK", self.work))
        self.monitor = Mock()
        self.runtime = self.stack.enter_context(patch.object(self.runner, "assert_runtime", return_value=self.monitor))
        self.supervise = self.stack.enter_context(patch.object(self.runner, "supervise"))

    def compile_request(self, **changes):
        return {"phase": "compile", "source": "int main(void){return 0;}", "link_args": [],
                "limits": dict(LIMITS), **changes}

    def run_request(self, **changes):
        return {"phase": "run", "artifact_base64": base64.b64encode(elf64()).decode(), "stdin": "fixture\n",
                "capabilities": [], "limits": dict(LIMITS), **changes}

    def success(self, **changes):
        return {"returncode": 0, "stdout": "", "stderr": "", "elapsed_ms": 1,
                "timed_out": False, "output_limited": False, "unavailable": False,
                "cancelled": False, "resource_limited": False, **changes}

    def compiler(self, result=None, artifact=None):
        def compile(command, **kwargs):
            self.assertEqual((self.work / "program.c").read_text(), "int main(void){return 0;}")
            if artifact is not None:
                (self.work / "program").write_bytes(artifact)
            return dict(result or self.success())
        self.supervise.side_effect = compile

    def test_compile_returns_only_verified_bounded_artifact_with_fixed_compiler_options(self):
        self.compiler(artifact=elf64())
        request = self.compile_request()
        result = self.runner.handle_request(request)
        self.assertEqual(base64.b64decode(result["artifact_base64"]), elf64())
        command = self.supervise.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/gcc")
        self.assertIn("-std=c11", command)
        self.assertIn("-Werror=implicit-function-declaration", command)
        self.assertEqual(command[-1], "-lm")
        kwargs = self.supervise.call_args.kwargs
        self.assertEqual((kwargs["timeout"], kwargs["stdout_limit"], kwargs["stderr_limit"]),
                         (LIMITS["compile_seconds"], LIMITS["diagnostics_bytes"], LIMITS["diagnostics_bytes"]))
        self.assertIs(kwargs["monitor"], self.monitor)

    def test_failed_or_interrupted_build_cannot_export_even_an_existing_artifact(self):
        for failure in ({"returncode": 1}, {"timed_out": True}, {"output_limited": True},
                        {"unavailable": True}, {"cancelled": True}, {"resource_limited": True}):
            self.compiler(result=self.success(**failure), artifact=elf64())
            with self.subTest(failure=failure):
                self.assertIsNone(self.runner.handle_request(self.compile_request())["artifact_base64"])

    def test_success_without_valid_elf_or_with_over_budget_artifact_is_rejected(self):
        for artifact in (None, b"not-elf", elf64() + b"x"):
            (self.work / "program").unlink(missing_ok=True)
            self.compiler(artifact=artifact)
            request = self.compile_request(limits={**LIMITS, "disk_bytes": len(elf64())})
            with self.subTest(artifact=artifact), self.assertRaises(ValueError):
                self.runner.handle_request(request)

    def test_compiler_symlink_cannot_export_an_outside_artifact(self):
        outside = self.work / "outside"
        outside.write_bytes(elf64())
        (self.work / "program").symlink_to(outside)
        self.compiler()
        with self.assertRaises(ValueError):
            self.runner.handle_request(self.compile_request())

    def test_run_installs_verified_owner_executable_and_forwards_only_requested_input(self):
        self.supervise.return_value = self.success(stdout="result")
        request = self.run_request()
        self.assertEqual(self.runner.handle_request(request)["stdout"], "result")
        executable = self.work / "program"
        self.assertEqual(executable.read_bytes(), elf64())
        self.assertEqual(executable.stat().st_mode & 0o777, 0o500)
        self.assertEqual(self.supervise.call_args.args[0], [str(executable)])
        kwargs = self.supervise.call_args.kwargs
        self.assertEqual((kwargs["stdin"], kwargs["timeout"], kwargs["stdout_limit"], kwargs["stderr_limit"]),
                         (request["stdin"], LIMITS["run_seconds"], LIMITS["stdout_bytes"], LIMITS["stderr_bytes"]))
        self.assertIs(kwargs["monitor"], self.monitor)
        self.assertTrue(kwargs["restrict_processes"])
        self.assertFalse(kwargs["allow_write"])

    def test_process_creation_requires_the_explicit_capability(self):
        self.supervise.return_value = self.success()
        self.runner.handle_request(self.run_request(capabilities=["process.spawn"]))
        self.assertFalse(self.supervise.call_args.kwargs["restrict_processes"])
        self.assertFalse(self.supervise.call_args.kwargs["allow_write"])

    def test_scratch_write_permission_requires_the_explicit_capability(self):
        self.supervise.return_value = self.success()
        for capabilities, allow_write in ((["files.read"], False), (["files.write"], True),
                                          (["files.read", "files.write", "process.spawn"], True)):
            with self.subTest(capabilities=capabilities):
                (self.work / "program").unlink(missing_ok=True)
                self.runner.handle_request(self.run_request(capabilities=capabilities))
                self.assertEqual(self.supervise.call_args.kwargs["allow_write"], allow_write)
                self.assertEqual(self.supervise.call_args.kwargs["restrict_processes"], "process.spawn" not in capabilities)

    def test_boundary_failure_prevents_creating_source_or_executable(self):
        self.runtime.side_effect = ValueError("container enforcement missing")
        for request in (self.compile_request(), self.run_request(), {"phase": "probe", "limits": dict(LIMITS)}):
            with self.subTest(phase=request["phase"]), self.assertRaises(ValueError):
                self.runner.handle_request(request)
            self.assertEqual(list(self.work.iterdir()), [])
            self.supervise.assert_not_called()

    def test_probe_reports_guarantees_only_after_runtime_and_seccomp_checks(self):
        with patch.object(self.runner, "install_seccomp") as seccomp, patch.object(Path, "is_file", return_value=True):
            result = self.runner.handle_request({"phase": "probe", "limits": dict(LIMITS)})
        seccomp.assert_called_once_with(False)
        self.runtime.assert_called_once_with(LIMITS)
        self.assertTrue(result["ready"])
        self.assertEqual(set(result["guarantees"]), {"filesystem", "credentials", "network", "cpu", "memory", "processes", "disk", "timeout", "output"})
        self.supervise.assert_not_called()


@unittest.skipUnless(LINUX, "Linux standalone worker protocol")
class EntryPointTests(unittest.TestCase):
    def invoke(self, payload):
        with tempfile.TemporaryDirectory() as directory:
            completed = subprocess.run([sys.executable, str(WORKER_PATH)], input=payload,
                                       capture_output=True, cwd=directory, timeout=3)
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(completed.stderr, b"")
        self.assertLess(len(completed.stdout), 4096)
        reply = json.loads(completed.stdout)
        self.assertIsInstance(reply, dict)
        self.assertTrue(reply.get("error") or reply.get("unavailable"))
        return reply

    def test_invalid_json_and_private_request_fields_are_not_echoed(self):
        for payload in (b"not-json-private-secret", b'{"phase":"private-secret"}',
                        json.dumps({"phase": "compile", "source": "private-secret", "link_args": [],
                                    "limits": dict(LIMITS), "secret": "private-secret"}).encode()):
            with self.subTest(payload=payload[:30]):
                self.assertNotIn("private-secret", str(self.invoke(payload)))

    def test_standalone_worker_has_no_authority_to_compile_without_container_enforcement(self):
        request = {"phase": "compile", "source": "int main(void){return 0;}",
                   "link_args": [], "limits": dict(LIMITS)}
        reply = self.invoke(json.dumps(request).encode())
        self.assertNotIn("artifact_base64", reply)
