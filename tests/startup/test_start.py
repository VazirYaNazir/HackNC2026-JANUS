"""Launcher regression checks using local files, sockets, and child processes.

These exercise launch orchestration without connecting to Cloudflare, creating
real desktop permissions, or claiming a macOS/Windows hardware check.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import sys
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("janus_launcher_under_test", ROOT / "start.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def eventually(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class TTY(io.StringIO):
    def isatty(self):
        return True


@contextlib.contextmanager
def http_service(status=200):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","service":"public"}')

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/health"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class EnvironmentAndRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()

    def registration(self, device="laptop", agent="a", controller="c"):
        return {"device_id": device, "owner_id": "owner", "agent_token": agent * 40,
                "controller_token": controller * 40}

    def test_environment_load_preserves_file_and_environment_precedence(self):
        path = self.root / "backend" / ".env"
        source = '# Existing registration and Gemini key must survive.\nGEMINI_API_KEY="local-key"\nAPP_ENV=development\n'
        path.write_text(source, encoding="utf-8")
        environment = {"GEMINI_API_KEY": "environment-key", "PATH": "/unchanged"}
        loaded = launcher.load_environment(path, environment)
        self.assertEqual(loaded["GEMINI_API_KEY"], "environment-key")
        self.assertEqual(loaded["APP_ENV"], "development")
        self.assertEqual(loaded["PATH"], "/unchanged")
        self.assertEqual(path.read_text(encoding="utf-8"), source)
        self.assertEqual(environment, {"GEMINI_API_KEY": "environment-key", "PATH": "/unchanged"})

    def test_missing_environment_file_preserves_existing_environment(self):
        self.assertEqual(launcher.load_environment(self.root / "missing.env", {"EXISTING": "value"}),
                         {"EXISTING": "value"})

    def test_existing_inline_credentials_are_reused_without_creating_registration_file(self):
        item = self.registration()
        environment = {"AGENT_DEVICES_JSON": json.dumps([item]), "GEMINI_API_KEY": "keep-key"}
        registration = launcher.provision_registration(self.root, environment, None)
        for field, value in item.items():
            self.assertEqual(getattr(registration, field), value)
        self.assertEqual(environment["GEMINI_API_KEY"], "keep-key")
        self.assertEqual(json.loads(environment["AGENT_DEVICES_JSON"]), [item])
        self.assertFalse((self.root / ".janus" / "devices.json").exists())

    def test_custom_registration_file_overrides_inline_and_resolves_relative_to_backend(self):
        item = self.registration("custom")
        path = self.root / "backend" / "registered.json"
        source = json.dumps([item])
        path.write_text(source, encoding="utf-8")
        environment = {"AGENT_DEVICES_FILE": "registered.json", "AGENT_DEVICES_JSON": "not-json"}
        registration = launcher.provision_registration(self.root, environment, "custom")
        self.assertEqual(registration.device_id, "custom")
        self.assertEqual(registration.agent_token, item["agent_token"])
        self.assertEqual(path.read_text(encoding="utf-8"), source)
        self.assertFalse((self.root / ".janus" / "devices.json").exists())

    def test_multiple_devices_require_explicit_selection(self):
        items = [self.registration("one"), self.registration("two", "b", "d")]
        environment = {"AGENT_DEVICES_JSON": json.dumps(items)}
        with self.assertRaises(ValueError):
            launcher.provision_registration(self.root, dict(environment), None)
        selected = launcher.provision_registration(self.root, dict(environment), "two")
        self.assertEqual(selected.device_id, "two")
        self.assertEqual(selected.controller_token, "d" * 40)
        with self.assertRaises(ValueError):
            launcher.provision_registration(self.root, dict(environment), "missing")

    def test_generated_credentials_are_private_distinct_and_reused(self):
        environment = {"AGENT_DEVICES_JSON": "[]"}
        first = launcher.provision_registration(self.root, environment, "personal-laptop")
        self.assertEqual(first.device_id, "personal-laptop")
        self.assertGreaterEqual(len(first.agent_token), 32)
        self.assertGreaterEqual(len(first.controller_token), 32)
        self.assertNotEqual(first.agent_token, first.controller_token)
        path = self.root / ".janus" / "devices.json"
        self.assertTrue(path.is_file())
        contents = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(contents), 1)
        self.assertEqual(contents[0]["agent_token"], first.agent_token)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        second = launcher.provision_registration(self.root, {"AGENT_DEVICES_JSON": "[]"}, "personal-laptop")
        self.assertEqual(second, first)

    def test_invalid_existing_credentials_fail_without_secret_disclosure(self):
        secret = "sensitive-secret-token-long-enough-for-role"
        invalid = [
            "invalid JSON",
            json.dumps({"devices": []}),
            json.dumps([self.registration("bad", "x", "x")]),
            json.dumps([{**self.registration(), "agent_token": "short"}]),
            json.dumps([self.registration(), self.registration()]),
            json.dumps([{**self.registration(), "device_id": "bad/device", "agent_token": secret}]),
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:20]):
                with self.assertRaises(ValueError) as error:
                    launcher.provision_registration(self.root, {"AGENT_DEVICES_JSON": raw}, None)
                self.assertNotIn(secret, str(error.exception))
                self.assertFalse((self.root / ".janus" / "devices.json").exists())


class ApprovalAndOriginTests(unittest.TestCase):
    def test_startup_is_denied_without_a_terminal_and_never_prompts(self):
        ask = Mock(return_value="yes")
        result = launcher.approve_startup(input_stream=io.StringIO(), input_fn=ask, output_stream=io.StringIO())
        self.assertFalse(result)
        ask.assert_not_called()

    def test_blank_no_and_other_answers_do_not_grant_permissions(self):
        for answer in ("", "no", "n", "maybe", "true"):
            with self.subTest(answer=answer):
                self.assertFalse(launcher.approve_startup(input_stream=TTY(), input_fn=lambda *_: answer,
                                                         output_stream=io.StringIO()))

    def test_explicit_yes_grants_startup_permissions(self):
        self.assertTrue(launcher.approve_startup(input_stream=TTY(), input_fn=lambda *_: "yes",
                                                output_stream=io.StringIO()))

    def test_public_origins_are_https_origins_without_embedded_credentials_or_paths(self):
        for value, expected in (("https://phone.example", "https://phone.example"),
                                ("https://phone.example/", "https://phone.example"),
                                ("https://phone.example:8443", "https://phone.example:8443")):
            self.assertEqual(launcher.validate_origin(value), expected)
        invalid = ("http://phone.example", "javascript:alert(1)", "https://user:secret@phone.example",
                   "https://phone.example/panel", "https://phone.example?token=secret",
                   "https://phone.example/#secret", "https://phone.example:99999", "https://", "https://phone.example bad",
                   "https://127.0.0.1", "https://localhost", "https://[::1]")
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                launcher.validate_origin(value)

    def test_tunnel_logs_capture_only_valid_cloudflare_origin(self):
        self.assertEqual(launcher.extract_tunnel_url("INF | https://valid-name.trycloudflare.com |"),
                         "https://valid-name.trycloudflare.com")
        for line in ("https://valid-name.trycloudflare.com.evil.example", "https://evil.example",
                     "http://valid-name.trycloudflare.com", "https://evil@valid-name.trycloudflare.com",
                     "https://valid-name.trycloudflare.com@evil.example", "https://.trycloudflare.com",
                     'ERR Post "https://api.trycloudflare.com/tunnel": EOF'):
            with self.subTest(line=line):
                self.assertIsNone(launcher.extract_tunnel_url(line))

    def test_atomic_json_replaces_valid_document_and_does_not_leave_staging_files(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "public-origins.json"
            first = {"backend": "https://first.trycloudflare.com", "pwa": None}
            second = {"backend": "https://second.trycloudflare.com", "pwa": "https://phone.trycloudflare.com"}
            launcher.atomic_json(path, first)
            launcher.atomic_json(path, second)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), second)
            self.assertEqual(list(Path(folder).iterdir()), [path])

    def test_failed_atomic_json_replacement_preserves_previous_document(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "public-origins.json"
            launcher.atomic_json(path, {"backend": "https://old.example"})
            original = path.read_bytes()
            with patch("os.replace", side_effect=OSError("fixture replacement failure")):
                with self.assertRaises(OSError):
                    launcher.atomic_json(path, {"backend": "https://new.example"})
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(folder).iterdir()), [path])


class CommandLineTests(unittest.TestCase):
    def test_default_ports_are_stable(self):
        options = launcher.parse_args([])
        self.assertEqual((options.backend_port, options.studio_port, options.pwa_port, options.admin_port),
                         (8000, 5173, 5174, 8001))
        self.assertEqual(options.studio, "electron")
        self.assertTrue(options.electron)

    def test_browser_override_disables_the_default_electron_interface(self):
        options = launcher.parse_args(["--studio=browser"])
        self.assertEqual(options.studio, "browser")
        self.assertFalse(options.electron)

    def test_explicit_operating_modes_and_ports_are_supported(self):
        options = launcher.parse_args(["--local-only", "--no-agent", "--no-browser", "--no-install", "--electron",
                                       "--device-id", "specific-laptop", "--backend-port", "18000", "--studio-port", "15173",
                                       "--pwa-port", "15174", "--admin-port", "18001", "--cloudflared", "/custom/cloudflared"])
        for flag in ("local_only", "no_agent", "no_browser", "no_install", "electron"):
            self.assertTrue(getattr(options, flag))
        self.assertEqual(options.device_id, "specific-laptop")
        self.assertEqual(options.cloudflared, "/custom/cloudflared")
        self.assertEqual((options.backend_port, options.studio_port, options.pwa_port, options.admin_port),
                         (18000, 15173, 15174, 18001))


class PolicyProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "agent").mkdir()
        self.path = self.root / "agent" / ".validation-policy.json"
        self.python_path = patch.object(sys, "path", [str(ROOT / "agent"), *sys.path])
        self.python_path.start()
        self.addCleanup(self.python_path.stop)

    def test_new_policy_has_all_capabilities_current_platform_dependencies_and_supported_maxima(self):
        from agent.isolation import ResourceLimits
        from agent.validation import CAPABILITIES, DEPENDENCIES
        from dataclasses import asdict
        for system in ("Darwin", "Windows"):
            with self.subTest(system=system):
                self.path.unlink(missing_ok=True)
                self.assertEqual(launcher.ensure_policy(self.root, system), self.path)
                policy = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(policy["jobs"], [])
                scope = policy["session"]
                self.assertEqual(scope["operations"], ["*"])
                self.assertEqual(set(scope["capabilities"]), CAPABILITIES)
                self.assertEqual(set(scope["dependencies"]), {name for name, details in DEPENDENCIES.items() if details[0] == system})
                self.assertEqual((scope["max_attempts"], scope["max_source_bytes"]), (3, 131072))
                self.assertEqual(scope["limits"], asdict(ResourceLimits()))
                self.assertTrue(scope["execute_authorized"] and scope["share_outputs"] and scope["side_effects"])
                self.assertNotIn("approved", policy)
                self.assertNotIn("approved", scope)
                if os.name == "posix":
                    self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_legacy_per_job_grants_are_preserved_when_adding_a_session_scope(self):
        job = {"job_id": "explicit-denial", "operation": "compute", "execute_authorized": False,
               "share_outputs": False, "side_effects": False, "output_protocol": "json-v1", "max_attempts": 1}
        self.path.write_text(json.dumps({"jobs": [job]}), encoding="utf-8")
        launcher.ensure_policy(self.root, "Darwin")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["jobs"], [job])
        self.assertIn("session", data)

    def test_existing_restricted_session_and_per_job_grants_are_not_overwritten(self):
        data = {"jobs": [{"job_id": "retained", "operation": "compute", "execute_authorized": False}],
                "session": {"operations": ["compute"], "capabilities": [], "dependencies": [],
                            "execute_authorized": False, "share_outputs": False, "side_effects": False,
                            "output_protocols": ["json-v1"], "max_attempts": 1, "max_source_bytes": 4096,
                            "limits": {"run_seconds": 1}}}
        original = json.dumps(data, separators=(",", ":"))
        self.path.write_text(original, encoding="utf-8")
        launcher.ensure_policy(self.root, "Darwin")
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)

    def test_invalid_existing_policy_fails_without_replacing_user_file(self):
        for data in ({"jobs": [{"job_id": "invalid", "operation": "compute", "max_attempts": 4}]},
                     {"jobs": [], "session": {"approved": True}}):
            with self.subTest(data=data):
                original = json.dumps(data)
                self.path.write_text(original, encoding="utf-8")
                with self.assertRaises(ValueError):
                    launcher.ensure_policy(self.root, "Darwin")
                self.assertEqual(self.path.read_text(encoding="utf-8"), original)


class DatabaseProvisioningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend" / "app").mkdir(parents=True)
        self.legacy = self.root / "backend" / "app" / "app.db"
        self.destination = self.root / ".janus" / "janus.sqlite"

    def create_legacy(self):
        with sqlite3.connect(self.legacy) as database:
            database.execute("CREATE TABLE legacy_phones (id TEXT PRIMARY KEY, token TEXT)")
            database.execute("INSERT INTO legacy_phones VALUES (?, ?)", ("retained-phone", "private-existing-phone-token"))

    def rows(self, path):
        with sqlite3.connect(path) as database:
            return database.execute("SELECT id, token FROM legacy_phones ORDER BY id").fetchall()

    def test_read_only_legacy_database_is_backed_up_privately_without_altering_original(self):
        self.create_legacy()
        if os.name == "posix":
            self.legacy.chmod(0o444)
        original, original_mode = self.legacy.read_bytes(), self.legacy.stat().st_mode
        environment = {}
        path = launcher.ensure_database(self.root, environment)
        self.assertEqual(path, self.destination)
        self.assertEqual(Path(environment["JANUS_DB_PATH"]), self.destination)
        self.assertEqual(self.rows(path), [("retained-phone", "private-existing-phone-token")])
        self.assertEqual(self.legacy.read_bytes(), original)
        self.assertEqual(self.legacy.stat().st_mode, original_mode)
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(path) as database:
            database.execute("INSERT INTO legacy_phones VALUES (?, ?)", ("new-phone", "new-token"))
        self.assertEqual(len(self.rows(path)), 2)
        self.assertEqual(self.legacy.read_bytes(), original)

    def test_existing_default_database_is_reused_without_recopying_legacy_rows(self):
        self.create_legacy()
        path = launcher.ensure_database(self.root, {})
        with sqlite3.connect(path) as database:
            database.execute("INSERT INTO legacy_phones VALUES (?, ?)", ("new-phone", "new-token"))
        original_state = path.read_bytes()
        with sqlite3.connect(self.legacy) as database:
            database.execute("DELETE FROM legacy_phones")
        second = launcher.ensure_database(self.root, {})
        self.assertEqual(second, path)
        self.assertEqual(second.read_bytes(), original_state)
        self.assertEqual(self.rows(second), [("new-phone", "new-token"), ("retained-phone", "private-existing-phone-token")])

    def test_backup_includes_committed_wal_rows(self):
        with sqlite3.connect(self.legacy) as writer:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE legacy_phones (id TEXT PRIMARY KEY, token TEXT)")
            writer.execute("INSERT INTO legacy_phones VALUES (?, ?)", ("wal-phone", "wal-token"))
            writer.commit()
            original = self.legacy.read_bytes()
            path = launcher.ensure_database(self.root, {})
            self.assertEqual(self.rows(path), [("wal-phone", "wal-token")])
            self.assertEqual(self.legacy.read_bytes(), original)

    def test_absolute_custom_database_is_preserved_at_its_configured_location(self):
        self.create_legacy()
        custom = self.root / "configured.sqlite"
        with sqlite3.connect(custom) as database:
            database.execute("CREATE TABLE custom_settings (value TEXT)")
            database.execute("INSERT INTO custom_settings VALUES ('retained')")
        original = custom.read_bytes()
        environment = {"JANUS_DB_PATH": str(custom)}
        self.assertEqual(launcher.ensure_database(self.root, environment), custom)
        self.assertEqual(custom.read_bytes(), original)
        self.assertEqual(Path(environment["JANUS_DB_PATH"]), custom)
        self.assertFalse(self.destination.exists())

    def test_relative_custom_database_resolves_from_backend_without_copying(self):
        path = self.root / "backend" / "custom.sqlite"
        with sqlite3.connect(path) as database:
            database.execute("CREATE TABLE retained (value TEXT)")
            database.execute("INSERT INTO retained VALUES ('custom')")
        original = path.read_bytes()
        environment = {"JANUS_DB_PATH": "custom.sqlite"}
        self.assertEqual(launcher.ensure_database(self.root, environment), path)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.destination.exists())

    def test_custom_database_with_missing_parent_fails_without_creating_or_relocating_it(self):
        path = self.root / "missing" / "custom.sqlite"
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            launcher.ensure_database(self.root, {"JANUS_DB_PATH": str(path)})
        self.assertFalse(path.exists())
        self.assertFalse(self.destination.exists())

    def test_partial_backup_failure_never_publishes_state_database_or_leaves_staging_files(self):
        self.create_legacy()
        original = self.legacy.read_bytes()
        real_connect = sqlite3.connect

        def connect(path, *args, **kwargs):
            connection = real_connect(path, *args, **kwargs)
            if "app.db" not in str(path):
                return connection
            source = MagicMock(wraps=connection)

            def fail_after_copy(destination, *args, **kwargs):
                connection.backup(destination, *args, **kwargs)
                raise sqlite3.OperationalError("fixture backup failure after copying")

            source.backup.side_effect = fail_after_copy
            source.__enter__.return_value = source
            source.__exit__.side_effect = lambda *_: connection.close()
            return source

        with patch.object(launcher.sqlite3, "connect", side_effect=connect):
            with self.assertRaises((sqlite3.Error, ValueError, RuntimeError, OSError)):
                launcher.ensure_database(self.root, {})
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.legacy.read_bytes(), original)
        if self.destination.parent.exists():
            self.assertEqual(list(self.destination.parent.iterdir()), [])


class PortAndReadinessTests(unittest.TestCase):
    def test_duplicate_and_invalid_ports_are_rejected(self):
        for ports in ([8000, 8000], [0], [-1], [65536]):
            with self.subTest(ports=ports), self.assertRaises((ValueError, RuntimeError, OSError)):
                launcher.check_ports(ports)

    def test_occupied_port_is_reported_before_launch(self):
        with socket.socket() as occupied:
            if os.name == "posix":
                occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            with self.assertRaises((ValueError, RuntimeError, OSError)):
                launcher.check_ports([occupied.getsockname()[1]])

    @unittest.skipUnless(os.name == "posix", "POSIX server socket reuse semantics")
    def test_recently_stopped_service_does_not_block_immediate_restart(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
                connection, _ = listener.accept()
                with connection:
                    # Closing from the server first leaves its port in TIME_WAIT.
                    connection.shutdown(socket.SHUT_WR)
                    self.assertEqual(client.recv(1), b"")
        launcher.check_ports([port])

    def test_available_port_check_releases_socket(self):
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        launcher.check_ports([port])
        with socket.socket() as subsequent:
            subsequent.bind(("127.0.0.1", port))

    def test_ready_http_service_is_detected(self):
        with http_service() as url:
            self.assertTrue(launcher.wait_http(url, timeout=1))

    def test_http_error_is_not_readiness(self):
        with http_service(status=503) as url:
            self.assertFalse(launcher.wait_http(url, timeout=0.15))

    def test_readiness_stops_when_process_has_exited(self):
        process = Mock()
        process.poll.return_value = 7
        with http_service() as url:
            self.assertFalse(launcher.wait_http(url, timeout=1, process=process))

    def test_missing_service_times_out_with_bounded_wait(self):
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{occupied.getsockname()[1]}/health"
            began = time.monotonic()
            self.assertFalse(launcher.wait_http(url, timeout=0.15))
            self.assertLess(time.monotonic() - began, 2)


class ManagedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def child(self, source, **options):
        child = launcher.ManagedProcess("fixture", [sys.executable, "-u", "-c", source],
                                        self.root, dict(os.environ), self.root / "logs", **options)
        self.addCleanup(child.stop)
        return child

    def logs(self):
        return "\n".join(path.read_text(encoding="utf-8") for path in (self.root / "logs").rglob("*") if path.is_file())

    def test_stdout_stderr_and_console_redact_registered_credentials(self):
        secret = "private-agent-credential-from-fixture"
        source = f"import sys; print('out {secret}', flush=True); print('err {secret}', file=sys.stderr, flush=True)"
        output = io.StringIO()
        child = self.child(source, redactions=(secret,), console=True)
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            child.start()
            self.assertTrue(eventually(lambda: child.poll() is not None))
            child.stop()
        logs = self.logs()
        self.assertIn("out ", logs)
        self.assertIn("err ", logs)
        self.assertNotIn(secret, logs)
        self.assertNotIn(secret, output.getvalue())

    def test_log_callback_receives_child_output_and_crash_is_visible(self):
        lines = []
        child = self.child("print('fixture ready', flush=True); raise SystemExit(23)", on_line=lines.append)
        child.start()
        self.assertTrue(eventually(lambda: child.poll() is not None and any("fixture ready" in line for line in lines)))
        self.assertEqual(child.poll(), 23)

    def test_input_is_delivered_privately_and_stdin_is_closed(self):
        secret = "private-startup-input-from-fixture"
        child = self.child("import sys; print('received '+sys.stdin.read(), flush=True)",
                           redactions=(secret,), input_data=secret + "\n")
        child.start()
        self.assertTrue(eventually(lambda: child.poll() is not None))
        child.stop()
        self.assertEqual(child.poll(), 0)
        self.assertIn("received ", self.logs())
        self.assertNotIn(secret, self.logs())

    def test_stop_is_idempotent_and_terminates_running_child(self):
        child = self.child("import time; print('ready', flush=True); time.sleep(60)")
        child.stop()
        child.start()
        self.assertTrue(eventually(lambda: "ready" in self.logs()))
        child.stop()
        self.assertIsNotNone(child.poll())
        child.stop()

    @unittest.skipUnless(os.name == "posix" and Path("/proc").exists(), "Linux process state required")
    def test_stop_terminates_child_process_group(self):
        lines = []
        source = ("import subprocess,sys,time; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  "print(p.pid,flush=True); time.sleep(60)")
        child = self.child(source, on_line=lines.append)
        child.start()
        self.assertTrue(eventually(lambda: any(line.strip().isdigit() for line in lines)))
        descendant = int(next(line.strip() for line in lines if line.strip().isdigit()))
        self.addCleanup(lambda: self.kill_if_running(descendant))
        child.stop()
        self.assertTrue(eventually(lambda: self.is_terminated(descendant)))

    @staticmethod
    def is_terminated(pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] == "Z"
        except FileNotFoundError:
            return True

    @staticmethod
    def kill_if_running(pid):
        if not ManagedProcessTests.is_terminated(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


class CloudflareTunnelTests(unittest.TestCase):
    def test_capture_uses_http2_and_the_configured_local_port(self):
        captured = []

        def make_service(name, command, cwd, env, logs_dir, **options):
            captured.append(command)
            service = Mock()
            service.start.side_effect = lambda: (options["on_line"]("INF https://first-name.trycloudflare.com"),
                                                options["on_line"]("INF Registered tunnel connection connIndex=0"), service)[2]
            service.poll.return_value = None
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "/private/cloudflared", 18000, {}, Path("/tmp/logs"))
            self.assertEqual(tunnel.start(), "https://first-name.trycloudflare.com")
            self.assertIn("http2", captured[0])
            self.assertEqual(captured[0][-1], "http://127.0.0.1:18000")
            tunnel.stop()
            self.assertIsNone(tunnel.url)
            tunnel.service.stop.assert_called_once()

    def test_invalid_url_and_early_process_exit_fail_and_cleanup(self):
        service = Mock()

        def make_service(*_, **options):
            service.start.side_effect = lambda: (options["on_line"]("https://false.trycloudflare.com.evil.example"), service)[1]
            service.poll.return_value = 1
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "cloudflared", 8000, {}, Path("/tmp/logs"))
            with self.assertRaises(RuntimeError):
                tunnel.start()
            self.assertIsNone(tunnel.url)
            service.stop.assert_called_once()

    def test_restarting_clears_old_url_and_captures_the_new_origin(self):
        services = []
        addresses = iter(("https://first-name.trycloudflare.com", "https://second-name.trycloudflare.com"))

        def make_service(*_, **options):
            service = Mock()
            address = next(addresses)
            service.start.side_effect = lambda: (options["on_line"](address),
                                                options["on_line"]("INF Registered tunnel connection connIndex=0"), service)[2]
            service.poll.return_value = None
            services.append(service)
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "cloudflared", 8000, {}, Path("/tmp/logs"))
            self.assertEqual(tunnel.start(), "https://first-name.trycloudflare.com")
            tunnel.stop()
            self.assertEqual(tunnel.start(), "https://second-name.trycloudflare.com")
            tunnel.stop()
            self.assertTrue(all(service.stop.call_count == 1 for service in services))

    def test_an_advertised_hostname_without_a_registered_connection_is_not_ready(self):
        service = Mock()

        def make_service(*_, **options):
            service.start.side_effect = lambda: (options["on_line"]("INF https://unconnected-name.trycloudflare.com"), service)[1]
            service.poll.return_value = 1
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "cloudflared", 8000, {}, Path("/tmp/logs"))
            with self.assertRaises(RuntimeError):
                tunnel.start()
            self.assertIsNone(tunnel.url)
            service.stop.assert_called_once()

    def test_blocked_quick_tunnel_api_errors_cannot_become_the_public_origin(self):
        service = Mock()

        def make_service(*_, **options):
            service.start.side_effect = lambda: (options["on_line"]('ERR Post "https://api.trycloudflare.com/tunnel": EOF'), service)[1]
            service.poll.return_value = 1
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "cloudflared", 8000, {}, Path("/tmp/logs"))
            with self.assertRaises(RuntimeError):
                tunnel.start()
            self.assertIsNone(tunnel.url)
            service.stop.assert_called_once()

    def test_registered_connection_before_hostname_is_also_ready(self):
        service = Mock()

        def make_service(*_, **options):
            service.start.side_effect = lambda: (options["on_line"]("INF Registered tunnel connection connIndex=0"),
                                                options["on_line"]("INF https://valid-name.trycloudflare.com"), service)[2]
            service.poll.return_value = None
            return service

        with patch.object(launcher, "ManagedProcess", side_effect=make_service):
            tunnel = launcher.CloudflareTunnel("fixture-tunnel", "cloudflared", 8000, {}, Path("/tmp/logs"))
            self.assertEqual(tunnel.start(), "https://valid-name.trycloudflare.com")
            tunnel.stop()


class CloudflareInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def download(self, payload=b"verified-cloudflared", *, system="Linux", architecture="x86_64", digest=None, address=None):
        platform_name = {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[system]
        suffix = ".tgz" if system == "Darwin" else ".exe" if system == "Windows" else ""
        asset = {"name": f"cloudflared-{platform_name}-amd64{suffix}",
                 "digest": "sha256:" + hashlib.sha256(payload).hexdigest() if digest is None else digest,
                 "browser_download_url": address or "https://github.com/cloudflare/cloudflared/releases/download/v2026/fixture"}
        release = {"tag_name": "v2026", "assets": [asset]}
        responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(payload)]
        with patch.object(launcher.shutil, "which", return_value=None), \
                patch.object(launcher.platform, "system", return_value=system), \
                patch.object(launcher.platform, "machine", return_value=architecture), \
                patch.object(launcher, "urlopen", side_effect=responses) as download, \
                contextlib.redirect_stdout(io.StringIO()):
            path = launcher.ensure_cloudflared(self.root, None)
        return Path(path), download

    def test_explicit_existing_executable_is_used_without_a_download(self):
        executable = self.root / "custom-cloudflared"
        executable.write_bytes(b"installed by user")
        with patch.object(launcher, "urlopen") as download:
            self.assertEqual(launcher.ensure_cloudflared(self.root, str(executable)), str(executable))
        download.assert_not_called()

    def test_missing_explicit_executable_fails_without_downloading(self):
        with patch.object(launcher, "urlopen") as download, self.assertRaises(ValueError):
            launcher.ensure_cloudflared(self.root, str(self.root / "missing"))
        download.assert_not_called()

    def test_verified_binary_is_installed_privately_and_validated_cache_avoids_downloads(self):
        path, download = self.download()
        self.assertEqual(path.read_bytes(), b"verified-cloudflared")
        self.assertEqual(download.call_count, 2)
        self.assertTrue(download.call_args_list[0].args[0].full_url.startswith("https://api.github.com/repos/cloudflare/cloudflared/"))
        metadata = json.loads(path.with_name("cloudflared.json").read_text())
        self.assertEqual(metadata["binary_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        with patch.object(launcher.shutil, "which", return_value=None), \
                patch.object(launcher.platform, "system", return_value="Linux"), \
                patch.object(launcher.platform, "machine", return_value="x86_64"), \
                patch.object(launcher, "urlopen") as download:
            self.assertEqual(launcher.ensure_cloudflared(self.root, None), str(path))
        download.assert_not_called()

    def test_tampered_cached_binary_is_replaced_by_a_fresh_verified_download(self):
        path, _ = self.download()
        path.write_bytes(b"tampered cache")
        replaced, download = self.download(b"fresh verified download")
        self.assertEqual(replaced, path)
        self.assertEqual(path.read_bytes(), b"fresh verified download")
        self.assertEqual(download.call_count, 2)

    def test_unverifiable_asset_or_checksum_mismatch_cannot_publish_a_binary(self):
        for digest in ("", "md5:wrong", "sha256:" + "0" * 64):
            with self.subTest(digest=digest), self.assertRaises(RuntimeError):
                self.download(digest=digest)
            self.assertFalse((self.root / ".janus" / "bin" / "cloudflared").exists())

    def test_download_must_come_from_the_official_release_origin(self):
        with self.assertRaises(RuntimeError):
            self.download(address="https://github.com.evil.example/cloudflare/cloudflared/releases/download/fixture")
        self.assertFalse((self.root / ".janus" / "bin" / "cloudflared").exists())

    def test_macos_archive_is_verified_then_only_the_executable_is_written(self):
        archive = io.BytesIO()
        payload = b"verified macOS binary"
        with tarfile.open(fileobj=archive, mode="w:gz") as package:
            member = tarfile.TarInfo("cloudflared")
            member.size = len(payload)
            package.addfile(member, io.BytesIO(payload))
        path, _ = self.download(archive.getvalue(), system="Darwin")
        self.assertEqual(path.read_bytes(), payload)
        record = json.loads(path.with_name("cloudflared.json").read_text())
        self.assertEqual(record["binary_sha256"], hashlib.sha256(payload).hexdigest())

    def test_windows_download_uses_an_exe_filename(self):
        path, _ = self.download(system="Windows", architecture="AMD64")
        self.assertEqual(path.name, "cloudflared.exe")
        self.assertEqual(path.read_bytes(), b"verified-cloudflared")


class LocalBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.host = "http://127.0.0.1:8000"
        self.token = "private-controller-token-only-for-this-local-bootstrap"
        self.studio = "http://127.0.0.1:5173"
        self.bootstrap = launcher.LocalBootstrap(self.studio, self.host, self.token)
        self.bootstrap.start()
        self.addCleanup(self.bootstrap.stop)

    def request(self, nonce=None, origin=None, method="POST"):
        headers = {"Content-Type": "application/json"}
        if origin is not None:
            headers["Origin"] = origin
        body = json.dumps({"nonce": nonce}).encode() if method == "POST" else None
        request = Request(self.bootstrap.origin + "/bootstrap", data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, response.headers, response.read()
        except HTTPError as response:
            return response.code, response.headers, response.read()

    def test_browser_link_contains_nonce_and_local_endpoint_without_credentials(self):
        address = self.bootstrap.browser_url()
        self.assertNotIn(self.token, address)
        self.assertNotIn(self.host, address)
        parsed = urlsplit(address)
        self.assertEqual(parsed.scheme + "://" + parsed.netloc, self.studio)
        values = parse_qs(parsed.fragment)
        self.assertEqual(values["janus-start"], [self.bootstrap.origin])
        self.assertEqual(values["nonce"], [self.bootstrap.nonce])
        self.assertEqual(self.bootstrap.server.server_address[0], "127.0.0.1")
        self.assertGreaterEqual(len(self.bootstrap.nonce), 32)

    def test_wrong_origin_missing_origin_and_bad_nonce_do_not_consume_bootstrap(self):
        for nonce, origin in ((self.bootstrap.nonce, None), (self.bootstrap.nonce, "https://evil.example"),
                              ("incorrect", self.studio), (None, self.studio)):
            with self.subTest(origin=origin, nonce=nonce):
                status, _, body = self.request(nonce, origin)
                self.assertIn(status, (400, 401, 403, 404))
                self.assertNotIn(self.token.encode(), body)
        status, headers, body = self.request(self.bootstrap.nonce, self.studio)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"host": self.host, "token": self.token})
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.studio)
        self.assertIn("no-store", headers.get("Cache-Control", ""))

    def test_successful_bootstrap_is_single_use(self):
        first, _, _ = self.request(self.bootstrap.nonce, self.studio)
        self.assertEqual(first, 200)
        status, _, body = self.request(self.bootstrap.nonce, self.studio)
        self.assertEqual(status, 410)
        self.assertNotIn(self.token.encode(), body)

    def test_get_cannot_disclose_controller_credential(self):
        status, _, body = self.request(self.bootstrap.nonce, self.studio, method="GET")
        self.assertNotEqual(status, 200)
        self.assertNotIn(self.token.encode(), body)
        status, _, body = self.request(self.bootstrap.nonce, self.studio)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["token"], self.token)

    def test_expired_bootstrap_never_discloses_credential(self):
        self.assertLessEqual(self.bootstrap.expires - time.monotonic(), 180)
        self.bootstrap.expires = time.monotonic() - 1
        status, _, body = self.request(self.bootstrap.nonce, self.studio)
        self.assertEqual(status, 410)
        self.assertNotIn(self.token.encode(), body)

    def test_preflight_allows_only_the_studio_origin(self):
        status, headers, body = self.request(origin=self.studio, method="OPTIONS")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), self.studio)
        self.assertEqual(headers.get("Access-Control-Allow-Methods"), "POST")
        self.assertNotIn(self.token.encode(), body)
        status, headers, body = self.request(origin="https://evil.example", method="OPTIONS")
        self.assertEqual(status, 403)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))
        self.assertNotIn(self.token.encode(), body)


class LauncherOrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        self.environment = {"AGENT_DEVICES_JSON": "[]"}
        self.services = []
        self.published = []
        self.tunnels = []
        self.events = []
        self.bootstrap = Mock()
        self.bootstrap.start.side_effect = lambda: (self.events.append("bootstrap"), self.bootstrap)[1]
        self.bootstrap.browser_url.return_value = "http://127.0.0.1:5173/#bootstrap"

    def make_service(self, name, command, cwd, env, logs_dir, **options):
        service = Mock()
        service.name = name
        service.command = command
        service.environment = env
        service.input_data = options.get("input_data")
        service.start.side_effect = lambda: (self.events.append("start-" + name), service)[1]
        service.poll.return_value = None
        if getattr(self, "fail_service_cleanup", False) and name == "pwa":
            service.stop.side_effect = OSError("fixture process cleanup failure")
        self.services.append(service)
        return service

    def run_launcher(self, arguments, *, approved=False, failure=None, recoveries=0, always_crash=False,
                     monitor_iterations=None, http_readiness=None, tunnel_failures=None, on_publish=None,
                     runtime_crashes=None):
        original_atomic = launcher.atomic_json
        owner = self
        readiness = {url: list(results) for url, results in (http_readiness or {}).items()}
        failed_starts = dict(tunnel_failures or {})
        self.http_snapshots = []
        self.monitor_clock = 0
        self.cancellation = threading.Event()
        self.signal_handlers = {}

        def check_http(url, *_args, **_kwargs):
            self.http_snapshots.append({"url": url, "published": list(owner.published),
                                        "tunnels": {tunnel.name: {"starts": tunnel.starts, "stops": tunnel.stop.call_count}
                                                    for tunnel in owner.tunnels}})
            if url in readiness:
                results = readiness[url]
                result = results.pop(0) if len(results) > 1 else results[0]
                return result(*_args, **_kwargs) if callable(result) else result
            return failure != "readiness"

        def publish(path, data):
            if Path(path).name == "public-urls.json":
                owner.published.append(json.loads(json.dumps(data)))
                if on_publish:
                    on_publish(data)
                if failure == "clear" and len(owner.published) > 1:
                    raise OSError("fixture state directory unavailable")
            original_atomic(path, data)

        class StopEvent:
            calls = 0
            stopped = False

            def wait(self, _):
                self.calls += 1
                owner.monitor_clock += 6
                limit = monitor_iterations if monitor_iterations is not None else (6 if always_crash else recoveries)
                return self.stopped or self.calls > limit

            def set(self):
                self.stopped = True
                owner.cancellation.set()

            def is_set(self):
                return self.stopped

        class FakeTunnel:
            def __init__(self, name, executable, port, env, logs_dir):
                self.name, self.port, self.starts = name, port, 0
                self.service = Mock()
                self.service.poll.side_effect = self.poll
                self.stop = Mock()
                self.crashes = (runtime_crashes or {}).get(name, recoveries if name == "tunnel-backend" else 0)
                self.started = False
                owner.tunnels.append(self)

            def poll(self):
                if not self.started:
                    return 1
                if (self.name == "tunnel-backend" and always_crash) or self.crashes > 0:
                    self.crashes -= 1
                    return 1
                return None

            def start(self):
                self.starts += 1
                self.started = False
                owner.events.append("start-" + self.name)
                if failure == "tunnel" and self.name == "tunnel-backend":
                    raise RuntimeError("fixture cloudflared startup failure")
                if failed_starts.get(self.name, 0):
                    failed_starts[self.name] -= 1
                    raise RuntimeError("fixture cloudflared startup failure")
                self.started = True
                return f"https://{self.name}-{self.starts}.trycloudflare.com"

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launcher, "ROOT", self.root))
            stack.enter_context(patch.object(launcher, "load_environment", return_value=dict(self.environment)))
            stack.enter_context(patch.object(launcher, "install_dependencies"))
            stack.enter_context(patch.object(launcher, "check_ports"))
            stack.enter_context(patch.object(launcher.platform, "system", return_value="Darwin" if approved else "Linux"))
            self.approval = stack.enter_context(patch.object(launcher, "approve_startup", return_value=approved))
            self.policy = stack.enter_context(patch.object(launcher, "ensure_policy", return_value=self.root / "agent" / ".validation-policy.json"))
            stack.enter_context(patch.object(launcher, "atomic_json", side_effect=publish))
            stack.enter_context(patch.object(launcher, "ManagedProcess", side_effect=self.make_service))
            stack.enter_context(patch.object(launcher, "LocalBootstrap", return_value=self.bootstrap))
            stack.enter_context(patch.object(launcher, "CloudflareTunnel", FakeTunnel))
            self.cloudflare_installer = stack.enter_context(patch.object(launcher, "ensure_cloudflared", return_value="fixture-cloudflared"))
            stack.enter_context(patch.object(launcher, "threading", SimpleNamespace(Event=StopEvent)))
            stack.enter_context(patch.object(launcher.time, "monotonic", side_effect=lambda: owner.monitor_clock))
            stack.enter_context(patch.object(launcher.signal, "getsignal", return_value=None))
            stack.enter_context(patch.object(launcher.signal, "signal", side_effect=self.signal_handlers.__setitem__))
            self.http = stack.enter_context(patch.object(launcher, "wait_http", side_effect=check_http))
            self.browser = stack.enter_context(patch.object(launcher.webbrowser, "open"))
            self.browser.side_effect = lambda *_: self.events.append("browser")
            self.output = io.StringIO()
            with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
                return launcher.main(arguments)

    def test_web_only_mode_launches_three_services_without_local_grants_or_agent(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent", "--no-browser", "--no-install"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.approval.assert_not_called()
        self.policy.assert_not_called()
        self.browser.assert_not_called()
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.stop.assert_called_once()
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})

    def test_approved_agent_receives_private_stdin_and_env_credentials_with_all_native_tools(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-browser", "--no-install"], approved=True), 0)
        self.approval.assert_called_once()
        self.policy.assert_called_once()
        agent = next(service for service in self.services if service.name == "agent")
        self.assertIn("--native-tools", agent.command)
        self.assertIn("--screen", agent.command)
        self.assertIn("--session-approval-stdin", agent.command)
        self.assertEqual(agent.input_data, "JANUS_SESSION_APPROVED\n")
        self.assertGreaterEqual(len(agent.environment["AGENT_TOKEN"]), 32)
        self.assertNotIn(agent.environment["AGENT_TOKEN"], " ".join(agent.command))
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))

    def test_custom_service_ports_are_used_by_commands_and_readiness_checks(self):
        args = ["--local-only", "--no-agent", "--no-browser", "--no-install", "--backend-port", "18000",
                "--studio-port", "15173", "--pwa-port", "15174", "--admin-port", "18001"]
        self.assertEqual(self.run_launcher(args), 0)
        self.assertIn("18000", self.services[0].command)
        self.assertIn("15173", self.services[1].command)
        self.assertIn("15174", self.services[2].command)
        self.assertEqual(self.services[0].environment["ADMIN_PORT"], "18001")
        self.assertEqual(self.services[0].environment["JANUS_TUNNEL_MODE"], "off")
        self.assertEqual(self.services[1].environment["VITE_BACKEND_URL"], "http://127.0.0.1:18000")
        self.assertEqual(self.services[2].environment["JANUS_PWA_HTTP"], "1")
        urls = [call.args[0] for call in self.http.call_args_list]
        self.assertEqual(urls[:3], ["http://127.0.0.1:18000/health", "http://127.0.0.1:15173", "http://127.0.0.1:15174"])

    def test_readiness_failure_stops_services_before_agent_or_bootstrap_can_start(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent", "--no-browser", "--no-install"], failure="readiness"), 1)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.start.assert_not_called()
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})

    def test_tunnel_startup_failure_keeps_local_services_and_bootstrap_available(self):
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], failure="tunnel"), 0)
        self.assertEqual(len(self.tunnels), 2)
        self.assertTrue(all(tunnel.stop.call_count >= 1 for tunnel in self.tunnels))
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.start.assert_called_once()
        self.assertTrue(all(document["backend"] is None for document in self.published))
        self.assertIn({"backend": None, "pwa": "https://tunnel-pwa-1.trycloudflare.com"}, self.published)
        self.assertIn("Phone connection unavailable", self.output.getvalue())
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})

    def test_pending_backend_public_url_preserves_the_healthy_pwa_and_both_connectors(self):
        backend = "https://tunnel-backend-1.trycloudflare.com"
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"],
                                           http_readiness={backend + "/health": [False], pwa: [True]}), 0)
        self.assertIn({"backend": None, "pwa": pwa}, self.published)
        self.assertTrue(all(document["backend"] is None for document in self.published))
        self.assertTrue(any(call.args[0] == pwa for call in self.http.call_args_list))
        self.assertTrue(all(tunnel.starts == 1 and tunnel.stop.call_count == 1 for tunnel in self.tunnels))
        self.bootstrap.start.assert_called_once()
        self.assert_local_tunnels_cleanup()

    def test_healthy_pwa_is_published_while_backend_public_readiness_is_still_waiting(self):
        backend = "https://tunnel-backend-1.trycloudflare.com"
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        pwa_published = threading.Event()
        backend_observations = []

        def publish(data):
            if data.get("pwa") == pwa:
                pwa_published.set()

        def backend_pending(*_args, **_kwargs):
            backend_observations.append(pwa_published.wait(1))
            return False

        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], on_publish=publish,
                                           http_readiness={backend + "/health": [backend_pending], pwa: [True]}), 0)
        self.assertEqual(backend_observations, [True])
        self.assertIn({"backend": None, "pwa": pwa}, self.published)
        self.assertTrue(all(tunnel.starts == 1 and tunnel.stop.call_count == 1 for tunnel in self.tunnels))
        self.assert_local_tunnels_cleanup()

    def test_interrupt_cancels_a_pending_worker_before_executor_cleanup_joins_it(self):
        backend = "https://tunnel-backend-1.trycloudflare.com"
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        backend_entered = threading.Event()
        cancellations = []

        def backend_pending(*_args, cancelled=None, **_kwargs):
            backend_entered.set()
            notified = self.cancellation.wait(1)
            cancellations.append(notified and cancelled is not None and cancelled.is_set())
            return False

        def interrupt_when_pwa_is_published(data):
            if data.get("pwa") == pwa:
                self.assertTrue(backend_entered.wait(1))
                self.signal_handlers[signal.SIGINT](signal.SIGINT, None)

        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"],
                                           on_publish=interrupt_when_pwa_is_published,
                                           http_readiness={backend + "/health": [backend_pending], pwa: [True]}), 0)
        self.assertEqual(cancellations, [True])
        self.assertTrue(all(tunnel.starts == 1 and tunnel.stop.call_count == 1 for tunnel in self.tunnels))
        self.assertTrue(all(document["backend"] is None for document in self.published))
        self.assert_local_tunnels_cleanup()

    def test_runtime_interrupt_during_backend_reconnect_prevents_restarting_the_sibling(self):
        recovered_backend = "https://tunnel-backend-2.trycloudflare.com"

        def interrupted_check(*_args, **_kwargs):
            self.signal_handlers[signal.SIGINT](signal.SIGINT, None)
            return False

        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], monitor_iterations=1,
                                           runtime_crashes={"tunnel-backend": 1, "tunnel-pwa": 1},
                                           http_readiness={recovered_backend + "/health": [interrupted_check]}), 0)
        by_name = {tunnel.name: tunnel for tunnel in self.tunnels}
        self.assertEqual(by_name["tunnel-backend"].starts, 2)
        self.assertEqual(by_name["tunnel-pwa"].starts, 1)
        self.assertEqual(by_name["tunnel-pwa"].stop.call_count, 1)
        self.assertTrue(all(document["backend"] != recovered_backend for document in self.published))
        self.assert_local_tunnels_cleanup()

    def test_registered_pending_connectors_are_rechecked_without_restart_and_recovery_is_published(self):
        backend = "https://tunnel-backend-1.trycloudflare.com"
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], monitor_iterations=2,
                                           http_readiness={backend + "/health": [False, True], pwa: [False]}), 0)
        self.assertIn({"backend": backend, "pwa": None}, self.published)
        self.assertTrue(all(document["pwa"] is None for document in self.published))
        backend_checks = [snapshot for snapshot in self.http_snapshots if snapshot["url"] == backend + "/health"]
        pwa_checks = [snapshot for snapshot in self.http_snapshots if snapshot["url"] == pwa]
        self.assertEqual(len(backend_checks), 2)
        self.assertGreaterEqual(len(pwa_checks), 2)
        for snapshot in backend_checks + pwa_checks:
            for tunnel in snapshot["tunnels"].values():
                self.assertLessEqual(tunnel["starts"], 1)  # The parallel sibling may not have started yet.
                self.assertEqual(tunnel["stops"], 0)
            name = "tunnel-backend" if snapshot["url"] == backend + "/health" else "tunnel-pwa"
            self.assertEqual(snapshot["tunnels"][name]["starts"], 1)
        self.assertTrue(all(tunnel.starts == 1 and tunnel.stop.call_count == 1 for tunnel in self.tunnels))
        self.assert_local_tunnels_cleanup()

    def test_failed_backend_start_is_retried_without_discarding_the_healthy_pwa(self):
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        backend = "https://tunnel-backend-2.trycloudflare.com"
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], monitor_iterations=1,
                                           tunnel_failures={"tunnel-backend": 1}), 0)
        partial = {"backend": None, "pwa": pwa}
        self.assertIn(partial, self.published)
        self.assertIn({"backend": backend, "pwa": pwa}, self.published)
        self.assertLess(self.published.index(partial), self.published.index({"backend": backend, "pwa": pwa}))
        self.assertTrue(all(document["pwa"] == pwa for document in self.published[self.published.index(partial):-1]))
        by_name = {tunnel.name: tunnel for tunnel in self.tunnels}
        self.assertEqual(by_name["tunnel-backend"].starts, 2)
        self.assertEqual(by_name["tunnel-pwa"].starts, 1)
        self.assertEqual(by_name["tunnel-pwa"].stop.call_count, 1)
        self.assert_local_tunnels_cleanup()

    def test_repeated_start_failures_are_bounded_without_stopping_the_healthy_pwa(self):
        pwa = "https://tunnel-pwa-1.trycloudflare.com"
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], monitor_iterations=6,
                                           failure="tunnel"), 0)
        by_name = {tunnel.name: tunnel for tunnel in self.tunnels}
        self.assertEqual(by_name["tunnel-backend"].starts, 4)
        self.assertEqual(by_name["tunnel-pwa"].starts, 1)
        self.assertEqual(by_name["tunnel-pwa"].stop.call_count, 1)
        partial = {"backend": None, "pwa": pwa}
        self.assertIn(partial, self.published)
        self.assertTrue(all(document == partial for document in self.published[self.published.index(partial):-1]))
        self.assert_local_tunnels_cleanup()

    def assert_local_tunnels_cleanup(self):
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.stop.assert_called_once()
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})

    def test_studio_bootstrap_and_browser_open_before_phone_tunnels_start(self):
        self.assertEqual(self.run_launcher(["--studio=browser", "--no-agent", "--no-install"]), 0)
        self.bootstrap.start.assert_called_once()
        self.browser.assert_called_once()
        self.assertLess(self.events.index("bootstrap"), self.events.index("start-tunnel-backend"))
        self.assertLess(self.events.index("browser"), self.events.index("start-tunnel-backend"))

    def test_configured_public_origins_are_preserved_without_starting_extra_tunnels(self):
        self.environment.update(PUBLIC_BACKEND_URL="https://backend.example", PWA_PUBLIC_URL="https://phone.example")
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"]), 0)
        self.cloudflare_installer.assert_not_called()
        self.assertEqual(self.published[0], {"backend": "https://backend.example", "pwa": "https://phone.example"})
        self.assertFalse(self.tunnels)

    def test_crashed_tunnel_origin_is_cleared_before_recovery_and_new_origin_is_published(self):
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], recoveries=1), 0)
        expected_initial = {"backend": "https://tunnel-backend-1.trycloudflare.com", "pwa": "https://tunnel-pwa-1.trycloudflare.com"}
        self.assertIn(expected_initial, self.published)
        first = self.published.index(expected_initial)
        self.assertEqual(self.published[first + 1], {"backend": None, "pwa": expected_initial["pwa"]})
        self.assertEqual(self.published[first + 2], {"backend": "https://tunnel-backend-2.trycloudflare.com", "pwa": expected_initial["pwa"]})
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})
        self.assertEqual(next(tunnel for tunnel in self.tunnels if tunnel.name == "tunnel-backend").starts, 2)
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))

    def test_tunnel_recovery_attempts_are_bounded_and_local_services_remain_available(self):
        self.assertEqual(self.run_launcher(["--no-agent", "--no-browser", "--no-install"], always_crash=True), 0)
        backend = next(tunnel for tunnel in self.tunnels if tunnel.name == "tunnel-backend")
        self.assertEqual(backend.starts, 4)
        self.bootstrap.start.assert_called_once()
        self.assertEqual(self.published[-1], {"backend": None, "pwa": None})
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))

    def test_failed_state_cleanup_still_stops_every_service_and_bootstrap(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent", "--no-browser", "--no-install"], failure="clear"), 0)
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.stop.assert_called_once()

    def test_one_service_cleanup_failure_does_not_skip_the_remaining_services(self):
        self.fail_service_cleanup = True
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent", "--no-browser", "--no-install"]), 0)
        self.assertTrue(all(service.stop.call_count == 1 for service in self.services))
        self.bootstrap.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
