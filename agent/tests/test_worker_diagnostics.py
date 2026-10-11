"""Check that guest diagnostics expose only fixed, public check results."""
import contextlib
import io
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from agent.isolation import ResourceLimits
from dataclasses import asdict
from agent.worker_diagnostics import DIAGNOSTIC_CHECKS, DIAGNOSTIC_CODES, DIAGNOSTIC_SOURCE


class WorkerDiagnosticsTests(unittest.TestCase):
    def run_source(self, *, runtime_error=None, seccomp_error=None, machine="aarch64", files=None, compiler=True, interfaces=None, network_error=None, proc_interfaces=None, proc_error=None, interface_flags=None, states=None):
        values = {"memory.max": "536870912", "memory.swap.max": "0", "pids.max": "16",
                  "cpu.max": "100000 100000", "cpu.max.burst": "0",
                  "status": "CapEff: 0\nCapBnd: 0\nNoNewPrivs: 1\n"}
        values.update(files or {})
        runtime = Mock(side_effect=runtime_error)
        seccomp = Mock(side_effect=seccomp_error)
        names = {"lo"} if interfaces is None else interfaces
        if states is None:
            states = []
            for name in sorted(names):
                flags = (interface_flags or {}).get(name, 9 if name == "lo" else 1)
                states.append({"name": name, "loopback": bool(flags & 8), "up": bool(flags & 1)})
        network = Mock(return_value=states, side_effect=network_error)
        isolation = Mock(side_effect=lambda rows: any(row["name"] == "lo" and row["loopback"] for row in rows)
                         and all(row["name"] == "lo" or not row["up"] for row in rows))
        proc_network = Mock(return_value={"lo"} if proc_interfaces is None else proc_interfaces, side_effect=proc_error)
        worker = {"assert_runtime": runtime, "install_seccomp": seccomp,
                  "_network_interface_states": network,
                  "_network_isolated": isolation,
                  "_proc_network_interfaces": proc_network,
                  "CgroupMonitor": Mock(return_value=SimpleNamespace(wall_allowance=lambda: 29)),
                  "_mounts": lambda: {"/": ({"ro"}, "overlay"), "/work": ({"rw"}, "tmpfs")}}
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("runpy.run_path", return_value=worker))
            stack.enter_context(patch("sys.stdin", io.StringIO(json.dumps(asdict(ResourceLimits())))))
            stack.enter_context(contextlib.redirect_stdout(output))
            stack.enter_context(patch.object(Path, "read_text", lambda path: values[path.name]))
            stack.enter_context(patch.object(Path, "exists", return_value=True))
            stack.enter_context(patch.object(Path, "is_file", return_value=compiler))
            stack.enter_context(patch.object(Path, "iterdir", side_effect=AssertionError("Network inspection must not use sysfs")))
            stack.enter_context(patch("os.getpid", return_value=1))
            stack.enter_context(patch("os.getuid", return_value=65532))
            stack.enter_context(patch("os.statvfs", return_value=SimpleNamespace(f_blocks=16384, f_frsize=4096)))
            stack.enter_context(patch("os.access", side_effect=lambda path, mode: str(path) != "/dev/shm"))
            stack.enter_context(patch("platform.machine", return_value=machine))
            stack.enter_context(patch.dict(os.environ, {"PATH": "/usr/bin:/bin", "HOME": "/work"}, clear=True))
            exec(compile(DIAGNOSTIC_SOURCE, "<trusted-worker-diagnostics>", "exec"), {})
        result = json.loads(output.getvalue())
        self.assertTrue({"checks", "code", "ready"} <= set(result) <= {"checks", "code", "ready", "network_interfaces"})
        self.assertEqual(set(result["checks"]), DIAGNOSTIC_CHECKS)
        self.assertTrue(all(type(value) is bool for value in result["checks"].values()))
        self.assertIn(result["code"], DIAGNOSTIC_CODES | {None})
        network.assert_called_once_with()
        proc_network.assert_called_once_with()
        if not network_error:
            isolation.assert_called_once_with(states)
        else:
            isolation.assert_not_called()
        facts = result.get("network_interfaces", [])
        self.assertLessEqual(len(facts), 32)
        for fact in facts:
            self.assertEqual(set(fact), {"name", "loopback", "up"})
            self.assertIsNotNone(re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", fact["name"]))
            self.assertIs(type(fact["loopback"]), bool)
            self.assertIs(type(fact["up"]), bool)
        return result, runtime, seccomp

    def test_interface_observations_report_only_kernel_names_and_public_flags(self):
        result, _, _ = self.run_source(interfaces={"lo", "eth0", "dummy0"}, interface_flags={"lo": 9, "eth0": 1, "dummy0": 0})
        self.assertEqual(result["network_interfaces"], [{"name": "dummy0", "loopback": False, "up": False},
                                                        {"name": "eth0", "loopback": False, "up": True},
                                                        {"name": "lo", "loopback": True, "up": True}])
        self.assertFalse(result["ready"])
        self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")

    def test_bounded_public_observations_are_forwarded_from_shared_worker_helper(self):
        names = {f"eth{index:02}" for index in range(31)} | {"lo"}
        result, _, _ = self.run_source(interfaces=names)
        self.assertEqual(len(result["network_interfaces"]), 32)
        self.assertEqual([fact["name"] for fact in result["network_interfaces"]], sorted(names))

    def test_flag_query_errors_block_readiness_without_exposing_private_details(self):
        for error in (OSError("private address/path"), ValueError("private invalid flags")):
            with self.subTest(error=type(error).__name__):
                result, _, _ = self.run_source(network_error=error, runtime_error=ValueError("Isolation required"))
                self.assertFalse(result["ready"])
                self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")
                self.assertNotIn("network_interfaces", result)
                self.assertNotIn("private", json.dumps(result))

    def test_dormant_linux_fallback_tunnels_are_isolated_with_canonical_loopback(self):
        result, runtime, _ = self.run_source(interfaces={"lo", "tunl0", "ip6tnl0", "sit0"},
                                            interface_flags={"lo": 9, "tunl0": 0, "ip6tnl0": 0, "sit0": 0},
                                            proc_interfaces={"lo", "tunl0", "ip6tnl0", "sit0"})
        self.assertTrue(result["ready"])
        self.assertTrue(result["checks"]["network"])
        self.assertFalse(result["checks"]["network_proc_loopback"])
        runtime.assert_called_once()

    def test_active_ethernet_or_tunnel_and_invalid_loopback_remain_blocked(self):
        cases = [("eth0", 1), ("tunl0", 1), ("ip6tnl0", 1), ("sit0", 1)]
        for name, flags in cases:
            with self.subTest(name=name):
                result, _, _ = self.run_source(interfaces={"lo", name}, interface_flags={"lo": 9, name: flags},
                                                runtime_error=ValueError("Isolation required"))
                self.assertFalse(result["ready"])
                self.assertFalse(result["checks"]["network"])
                self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")
        for states in ([], [{"name": "lo", "loopback": False, "up": True}], [{"name": "dummy0", "loopback": False, "up": False}]):
            with self.subTest(states=states):
                result, _, _ = self.run_source(states=states, runtime_error=ValueError("Isolation required"))
                self.assertFalse(result["ready"])
                self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")

    def test_network_diagnostic_uses_process_namespace_and_rejects_unreadable_evidence(self):
        for options in ({"interfaces": {"lo", "eth0"}}, {"network_error": OSError("private network path")}):
            result, _, _ = self.run_source(runtime_error=ValueError("Network isolation required"), **options)
            self.assertFalse(result["checks"]["network"])
            self.assertFalse(result["ready"])
            self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")
            self.assertEqual(result["checks"]["network_kernel_query"], "network_error" not in options)
            self.assertNotIn("private network path", json.dumps(result))

    def test_proc_read_errors_are_informational_when_kernel_confirms_loopback(self):
        for error in (OSError("secret /private/network"), ValueError("secret malformed counters")):
            with self.subTest(error=type(error).__name__):
                result, runtime, _ = self.run_source(proc_error=error)
                self.assertTrue(result["ready"])
                self.assertIsNone(result["code"])
                self.assertTrue(result["checks"]["network"])
                self.assertTrue(result["checks"]["network_kernel_query"])
                self.assertFalse(result["checks"]["network_proc_readable"])
                self.assertFalse(result["checks"]["network_proc_loopback"])
                runtime.assert_called_once()
                self.assertNotIn("secret", json.dumps(result))

    def test_extra_proc_interfaces_do_not_override_authoritative_kernel_namespace(self):
        result, runtime, _ = self.run_source(proc_interfaces={"lo", "eth0"})
        self.assertTrue(result["ready"])
        self.assertTrue(result["checks"]["network"])
        self.assertTrue(result["checks"]["network_proc_readable"])
        self.assertFalse(result["checks"]["network_proc_loopback"])
        runtime.assert_called_once()

    def test_kernel_network_failure_is_never_masked_by_other_successful_checks(self):
        for options in ({"interfaces": {"lo", "eth0"}}, {"network_error": OSError("private kernel error")}):
            with self.subTest(options=options):
                result, _, _ = self.run_source(**options)
                self.assertFalse(result["ready"])
                self.assertEqual(result["code"], "WORKER_NETWORK_UNAVAILABLE")
                self.assertTrue(result["checks"]["network_proc_readable"])
                self.assertTrue(result["checks"]["network_proc_loopback"])
                self.assertNotIn("private kernel error", json.dumps(result))

    def test_native_arm64_and_x86_success_use_existing_worker_checks(self):
        for machine in ("aarch64", "arm64", "x86_64"):
            with self.subTest(machine=machine):
                result, runtime, seccomp = self.run_source(machine=machine)
                self.assertTrue(result["ready"])
                self.assertTrue(all(result["checks"].values()))
                runtime.assert_called_once_with(asdict(ResourceLimits()))
                seccomp.assert_called_once_with(False)

    def test_private_runtime_and_seccomp_errors_are_never_reported(self):
        for field in ("runtime_error", "seccomp_error"):
            with self.subTest(field=field):
                result, _, _ = self.run_source(**{field: OSError("secret credential /private/path")})
                self.assertFalse(result["ready"])
                self.assertNotIn("secret", json.dumps(result))
                self.assertEqual(result["code"], "WORKER_RUNTIME_UNAVAILABLE" if field == "runtime_error" else "WORKER_SECCOMP_UNAVAILABLE")

    def test_effective_limits_identify_independent_failures(self):
        cases = (("memory.max", "max", "memory"), ("memory.swap.max", "1", "swap"),
                 ("pids.max", "max", "processes"), ("cpu.max", "max 100000", "cpu_quota"),
                 ("cpu.max", "100000 200000", "cpu_period"), ("cpu.max.burst", "1", "cpu_burst"))
        for name, value, check in cases:
            with self.subTest(check=check):
                result, _, _ = self.run_source(files={name: value}, runtime_error=ValueError("private"))
                self.assertFalse(result["checks"][check])
                self.assertFalse(result["ready"])

    def test_compiler_and_architecture_have_fixed_failure_codes(self):
        for argument, expected in (({"compiler": False}, "WORKER_COMPILER_UNAVAILABLE"),
                                   ({"machine": "unsupported"}, "WORKER_ARCHITECTURE_UNSUPPORTED")):
            result, _, _ = self.run_source(**argument)
            self.assertEqual(result["code"], expected)
            self.assertFalse(result["ready"])

    def test_unreadable_image_produces_only_fixed_failure_code(self):
        output = io.StringIO()
        with patch("runpy.run_path", side_effect=OSError("secret /home/user/path")), patch("sys.stdin", io.StringIO("{}")), contextlib.redirect_stdout(output):
            exec(compile(DIAGNOSTIC_SOURCE, "<trusted-worker-diagnostics>", "exec"), {})
        result = json.loads(output.getvalue())
        self.assertEqual(result["code"], "WORKER_DIAGNOSTIC_UNAVAILABLE")
        self.assertFalse(result["ready"])
        self.assertEqual(set(result["checks"]), DIAGNOSTIC_CHECKS)
        self.assertNotIn("secret", output.getvalue())


if __name__ == "__main__":
    unittest.main()
