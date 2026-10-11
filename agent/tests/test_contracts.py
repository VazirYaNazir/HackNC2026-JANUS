"""Detailed manifests, local grants, C11 source rules, limits, and output framing."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from agent.compiler import CCompiler
from agent.isolation import BoundaryUnavailable, IsolationBoundary, REQUIRED_GUARANTEES, ResourceLimits
from agent.process import ProcessResult
from agent.validation import CAPABILITIES, CodeRevision, ExecutionPolicy, Fixture, Job, MAX_SOURCE_BYTES, architecture, check_source, dependency_args, load_policies, valid_output
from tests.support import COMPILER, SOURCE, job


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.job = Job.parse(job())
        self.policy = ExecutionPolicy("job-123", "compute", side_effects=False)

    def test_manifest_rejects_every_missing_field_extra_remote_policy_and_wrong_type(self):
        for key in job():
            data = job()
            data.pop(key)
            with self.subTest(missing=key), self.assertRaises(ValueError):
                Job.parse(data)
        changes = [{"type": "wrong"}, {"job_id": "path/escape"}, {"job_id": "é"}, {"job_id": "x" * 129}, {"attempt": 2}, {"language": "cpp"}, {"operation": "Bad operation"}, {"platform": []}, {"architecture": 1}, {"architecture": "x" * 33}, {"source": None}, {"capabilities": ["unknown"]}, {"dependencies": ["mac-system", "mac-system"]}, {"capabilities": "network"}, {"authorized": True}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                Job.parse(job(**change))
        for data in (None, [], "source"):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Job.parse(data)

    def test_direct_job_instances_cannot_bypass_immutable_contract(self):
        for change in ({"job_id": 1}, {"attempt": True}, {"operation": ""}, {"source": b"source"}, {"platform": None}, {"capabilities": {"network"}}, {"dependencies": frozenset({"unknown"})}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.job, **change)

    def test_revision_cannot_add_scope_or_use_noninteger_attempt_or_nontext_source(self):
        data = {"type": "code_revision", "job_id": "job-123", "attempt": 2, "language": "c11", "source": SOURCE}
        self.assertEqual(CodeRevision.parse(data).source, SOURCE)
        for change in ({"type": "validate_job"}, {"attempt": True}, {"source": None}, {"capabilities": ["admin"]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                CodeRevision.parse({**data, **change})

    def test_local_grants_reject_mutable_values_invalid_flags_and_unbounded_inputs(self):
        changes = [{"job_id": "invalid/id"}, {"operation": ""}, {"execute_authorized": 1}, {"share_outputs": "true"}, {"side_effects": None}, {"capabilities": {"network"}}, {"dependencies": frozenset({"unknown"})}, {"capabilities": frozenset({"unknown"})}, {"fixtures": []}, {"limits": {}}, {"max_attempts": 0}, {"max_attempts": 4}, {"max_attempts": True}, {"max_source_bytes": MAX_SOURCE_BYTES + 1}, {"max_source_bytes": 0}, {"output_protocol": "unknown"}, {"fixtures": (Fixture(),) * 9}, {"execution_stdin": 1}, {"execution_stdin": "x" * 65537}, {"fixtures": ("invalid",)}, {"fixtures": (Fixture(stdin=1),)}, {"fixtures": (Fixture(stdout="x" * 65537),)}, {"fixtures": (Fixture(returncode=True),)}]
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                replace(self.policy, **change)

    def test_source_accepts_normal_entrypoints_and_ignores_comments_and_literals(self):
        for source in (SOURCE, "int main(int argc, char **argv) {return argc;}", "int main(int argc, char *argv[]) {return argc;}", '/* int main(void) {} */\n#include <stdio.h>\nint main(void) {puts("int main(void) {}");return 0;}', SOURCE + "\n// private absolute /Users/fixture is a comment"):
            with self.subTest(source=source):
                self.assertEqual(check_source(source, self.job, self.policy), source.encode())

    def test_source_rejects_ambiguous_entrypoints_paths_and_dependency_directives(self):
        sources = ["", "int main() {return 0;}", SOURCE + "\n" + SOURCE, SOURCE + "\0", SOURCE + "\ud800", "x" * (MAX_SOURCE_BYTES + 1), '#include "private.h"\n' + SOURCE, "#include HEADER\n" + SOURCE, "#include_next <stdio.h>\n" + SOURCE, "#embed <data>\n" + SOURCE, "#import <stdio.h>\n" + SOURCE, "#include </private.h>\n" + SOURCE, "#include <sys/../private.h>\n" + SOURCE, "#include <unknown.h>\n" + SOURCE]
        for path in ("/Users/fixture/private", "~/private", "C:\\Users\\fixture"):
            sources.append('int main(void) {const char *path="' + path + '";return 0;}')
        for source in sources:
            with self.subTest(source=repr(source)[:100]), self.assertRaises(ValueError):
                check_source(source, self.job, self.policy)

    def test_utf8_limit_counts_bytes_and_exact_boundary_is_accepted(self):
        source = SOURCE + "/*" + "x" * (MAX_SOURCE_BYTES - len(SOURCE) - 4) + "*/"
        self.assertEqual(len(check_source(source, self.job, self.policy)), MAX_SOURCE_BYTES)
        with self.assertRaises(ValueError):
            check_source(source + "é", self.job, self.policy)

    def test_declared_headers_link_only_fixed_available_dependencies(self):
        declarations = [("Darwin", "iokit", "IOKit/IOKitLib.h"), ("Darwin", "coreaudio", "CoreAudio/CoreAudio.h"), ("Darwin", "coreaudio", "CoreFoundation/CoreFoundation.h"), ("Darwin", "mac-system", "sys/types.h"), ("Windows", "win32", "windows.h")]
        for system, dependency, header in declarations:
            with self.subTest(dependency=dependency):
                manifest = Job.parse(job(platform=system, dependencies=[dependency]))
                policy = replace(self.policy, dependencies=frozenset({dependency}))
                self.assertTrue(check_source(f"#include <{header}>\n" + SOURCE, manifest, policy))
                compiler = CCompiler("msvc" if system == "Windows" else "clang", "fixture", system)
                args = dependency_args(manifest, compiler)
                self.assertTrue(all(arg.endswith(".lib") for arg in args) if system == "Windows" else all(args[index] == "-framework" for index in range(0, len(args), 2)))
        win_job = Job.parse(job(platform="Windows", dependencies=["win32"]))
        self.assertTrue(all(arg.startswith("-l") for arg in dependency_args(win_job, CCompiler("gcc", "fixture", "Windows"))))

    def test_obvious_capability_requires_job_declaration_even_if_local_grant_is_broader(self):
        for capability, expression in (("process.spawn", 'system("command")'), ("files.write", 'remove("relative")'), ("network", "connect(0,0,0)")):
            policy = replace(self.policy, capabilities=frozenset({capability}))
            source = f"int main(void) {{{expression};return 0;}}"
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                check_source(source, self.job, policy)
            declared = Job.parse(job(capabilities=[capability]))
            self.assertTrue(check_source(source, declared, policy))

    def test_architecture_aliases_and_unknown_value_normalization(self):
        for name, expected in (("AMD64", "x86_64"), ("aarch64", "arm64"), ("i386", "x86"), ("arm64", "arm64"), ("UNKNOWN", "unknown")):
            self.assertEqual(architecture(name), expected)

    def test_output_limits_flags_and_versioned_json_are_enforced(self):
        for outcome in (ProcessResult(0, timed_out=True), ProcessResult(0, output_limited=True), ProcessResult(None, unavailable=True), ProcessResult(0, cancelled=True), ProcessResult(0, "x" * 65537), ProcessResult(0, "", "x" * 16385)):
            self.assertFalse(valid_output(outcome, self.policy))
        structured = replace(self.policy, output_protocol="json-v1")
        for output in ('{"version":1,"result":2}', '{"version":1}'):
            self.assertTrue(valid_output(ProcessResult(0, output), structured))
        for output in ('{"version":true}', '{"version":2}', "[]", "null", "{}", "bad", '{"version":1}\n{"version":1}'):
            self.assertFalse(valid_output(ProcessResult(0, output), structured))

    def test_policy_file_schema_duplicates_fixture_fields_and_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "policy.json"
            self.assertEqual(load_policies(None), {})
            bad = [None, [], {}, {"jobs": None}, {"jobs": [{}]}, {"jobs": [{"job_id": "j", "operation": "compute", "unknown": 1}]}, {"jobs": [{"job_id": "j", "operation": "compute", "fixtures": None}]}, {"jobs": [{"job_id": "j", "operation": "compute", "fixtures": [{"private_path": "bad"}]}]}, {"jobs": [{"job_id": "j", "operation": "compute"}] * 2}, {"jobs": [{}] * 257}, {"jobs": [{"job_id": "j", "operation": "compute", "side_effects": "false"}]}]
            for data in bad:
                with self.subTest(data=str(data)[:80]), self.assertRaises(ValueError):
                    path.write_text(json.dumps(data))
                    load_policies(str(path))
            path.write_text("x" * (256 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, "too large"):
                load_policies(str(path))
            path.write_text(json.dumps({"jobs": [{"job_id": "j", "operation": "compute", "capabilities": ["files.read"], "fixtures": [{"stdin": "synthetic", "stdout": "answer"}], "execution_stdin": "private runtime"}]}))
            policy = load_policies(str(path))["j"]
            self.assertEqual(policy.fixtures, (Fixture("synthetic", "answer"),))
            self.assertNotIn("private runtime", repr(policy))


class IsolationTests(unittest.IsolatedAsyncioTestCase):
    def test_every_resource_limit_rejects_zero_negative_bool_or_noninteger_budget(self):
        for name in ("compile_seconds", "run_seconds", "diagnostics_bytes", "stdout_bytes", "stderr_bytes", "memory_bytes", "disk_bytes", "processes", "cpu_seconds"):
            for value in (0, -1, True, "1"):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    ResourceLimits(**{name: value})
        for name, value in (("compile_seconds", 31), ("run_seconds", 4), ("run_seconds", float("nan")), ("compile_seconds", float("inf")), ("memory_bytes", 1.5)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                ResourceLimits(**{name: value})

    async def test_default_boundary_always_denies_build_and_run(self):
        boundary = IsolationBoundary()
        self.assertFalse(boundary.can_build())
        self.assertFalse(boundary.can_run())
        with self.assertRaises(BoundaryUnavailable):
            await boundary.compile(COMPILER, Path("source"), Path("output"), ResourceLimits(), ())
        with self.assertRaises(BoundaryUnavailable):
            await boundary.run(Path("output"), "", ResourceLimits(), frozenset())

    def test_all_guarantees_and_capability_support_are_required(self):
        boundary = IsolationBoundary()
        boundary.build_guarantees = REQUIRED_GUARANTEES
        boundary.run_guarantees = REQUIRED_GUARANTEES
        self.assertTrue(boundary.can_build())
        self.assertTrue(boundary.can_run())
        self.assertFalse(boundary.can_run(frozenset({"network"})))
        boundary.run_capabilities = frozenset({"network"})
        self.assertTrue(boundary.can_run(frozenset({"network"})))
        for missing in REQUIRED_GUARANTEES:
            boundary.build_guarantees = REQUIRED_GUARANTEES - {missing}
            boundary.run_guarantees = REQUIRED_GUARANTEES - {missing}
            self.assertFalse(boundary.can_build())
            self.assertFalse(boundary.can_run())
