import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agent_runtime.config import DeviceRegistry, Registration, RuntimeSettings
from app.agent_runtime.models import Envelope, GeneratedProgram, GenerationRequest, RepairPayload, safe_diagnostics
from tests.support import GOOD, program, registry, request


class ContractTests(unittest.TestCase):
    def test_exact_source_byte_limit_and_invalid_source(self):
        for source in ("é" * 65537, "\0", "\ud800"):
            with self.subTest(source_length=len(source)), self.assertRaises(ValueError):
                program(source=source)
        self.assertEqual(len(program(source="é" * 65536).source.encode()), 131072)

    def test_scope_and_declarations_never_accept_cloud_permission_flags(self):
        data = request().model_dump()
        for change in ({"capabilities": ["root"]}, {"capabilities": ["network", "network"]}, {"dependencies": ["win32"]}, {"execute_authorized": True}, {"share_outputs": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                GenerationRequest.model_validate({**data, **change})
        for change in ({"platform": "Windows"}, {"capabilities": ["network"]}, {"output_protocol": "text"}, {"operation": "other"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                GeneratedProgram.model_validate({**program().model_dump(), **change}).check_scope(request())

    def test_manifest_matches_existing_agent_contract(self):
        from agent.validation import Job, check_source, ExecutionPolicy
        job = Job.parse(program().manifest("job-1"))
        self.assertEqual(check_source(job.source, job, ExecutionPolicy("job-1", "compute")), GOOD.encode())

    def test_provider_declaration_order_has_no_semantic_effect_but_duplicates_are_rejected(self):
        req = GenerationRequest(**{**request().model_dump(), "capabilities": ["files.read", "network"]})
        generated = program(req)
        generated.capabilities = ["network", "files.read"]
        generated.check_scope(req)
        generated.capabilities = ["network", "network", "files.read"]
        with self.assertRaises(ValueError):
            generated.check_scope(req)

    def test_all_agent_diagnostic_categories_are_supported_and_private_text_is_rejected(self):
        from agent.validation import sanitize_diagnostics
        raw = "\n".join(f"/private/home/person/program.c:1:2: error: {line}" for line in (
            "undeclared SECRET", "unknown type SECRET", "undefined reference PRIVATE", "file not found /home/private",
            "too few arguments PRIVATE", "incompatible PRIVATE", "redefinition PRIVATE", "expected PRIVATE", "opaque PRIVATE"))
        safe = sanitize_diagnostics(raw)
        self.assertEqual(safe_diagnostics(safe), safe)
        self.assertEqual(safe_diagnostics(sanitize_diagnostics("error: undefined reference SECRET")), "compiler: error: undefined symbol")
        for value in (raw, "SECRET", "x" * 16385, None):
            with self.subTest(value_type=type(value)), self.assertRaises(ValueError):
                safe_diagnostics(value)

    def test_protocol_is_versioned_strict_and_device_scoped(self):
        frame = {"version": 2, "type": "job_ack", "device_id": "device-1", "job_id": "job-1", "state": "ACCEPTED", "payload": {}}
        Envelope.model_validate(frame)
        for change in ({"version": 1}, {"version": True}, {"device_id": "../bad"}, {"payload": []}, {"extra": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                Envelope.model_validate({**frame, **change})

    def test_device_tokens_have_separate_roles_and_scopes(self):
        devices = registry()
        identity = devices.agent("device-1", "agent-1-" + "a" * 32)
        self.assertEqual(identity.owner_id, "owner-1")
        self.assertIsNone(devices.agent("device-2", "agent-1-" + "a" * 32))
        self.assertIsNone(devices.controller("agent-1-" + "a" * 32))
        self.assertIsNone(devices.agent("device-1", "control-1-" + "b" * 32))
        self.assertIsNone(devices.controller("wrong"))

    def test_registration_file_and_environment_errors_never_echo_tokens(self):
        data = [{"device_id": "one", "owner_id": "owner", "agent_token": "a" * 32, "controller_token": "b" * 32}]
        with patch.dict(os.environ, {"AGENT_DEVICES_JSON": json.dumps(data)}, clear=True):
            self.assertEqual(len(DeviceRegistry.from_env().devices), 1)
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "devices.json"
            file.write_text(json.dumps(data))
            with patch.dict(os.environ, {"AGENT_DEVICES_FILE": str(file)}, clear=True):
                self.assertEqual(len(DeviceRegistry.from_env().devices), 1)
            file.write_text("private-token")
            with patch.dict(os.environ, {"AGENT_DEVICES_FILE": str(file)}, clear=True), self.assertRaisesRegex(ValueError, "^Invalid agent registration configuration$"):
                DeviceRegistry.from_env()
        # Windows cannot hold an environment variable over 32767 characters, so the oversize case there is
        # exercised through the registration file instead (same 64 KiB limit, separate code path).
        raws = ["{}", "secret", json.dumps(data * 2)] + ([] if os.name == "nt" else ["x" * 65537])
        for raw in raws:
            with patch.dict(os.environ, {"AGENT_DEVICES_JSON": raw}, clear=True), self.assertRaisesRegex(ValueError, "^Invalid agent registration configuration$"):
                DeviceRegistry.from_env()
        with tempfile.TemporaryDirectory() as tmp:
            oversize = Path(tmp) / "oversize.json"
            oversize.write_text("x" * 65537)
            with patch.dict(os.environ, {"AGENT_DEVICES_FILE": str(oversize)}, clear=True), self.assertRaisesRegex(ValueError, "^Invalid agent registration configuration$"):
                DeviceRegistry.from_env()
        with self.assertRaises(ValueError):
            DeviceRegistry([Registration(**{**data[0], "controller_token": "a" * 32})])

    def test_runtime_configuration_bounds(self):
        for change in ({"max_jobs_per_device": 5}, {"max_active_jobs": True}, {"job_timeout": float("nan")}, {"heartbeat_timeout": 0}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                RuntimeSettings(**change)
