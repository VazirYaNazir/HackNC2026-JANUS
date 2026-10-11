"""Compatibility modes, private approval, and optional messaging regressions.

No real desktop permission, Photon account, or Cloudflare connection is used.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("janus_launcher_compat_under_test", ROOT / "start.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


class PrivateApprovalTests(unittest.TestCase):
    def test_private_pipe_requires_the_exact_desktop_approval_marker(self):
        self.assertTrue(launcher.consume_startup_approval(io.StringIO("JANUS_STARTUP_APPROVED\n")))
        for value in ("", "yes\n", "JANUS_SESSION_APPROVED\n", "janus_startup_approved\n",
                      "JANUS_STARTUP_APPROVED", " JANUS_STARTUP_APPROVED\n",
                      "JANUS_STARTUP_APPROVED \n", "JANUS_STARTUP_APPROVED\r\n"):
            with self.subTest(value=value):
                self.assertFalse(launcher.consume_startup_approval(io.StringIO(value)))

    def test_tty_cannot_masquerade_as_private_approval_and_is_never_read(self):
        source = Mock()
        source.isatty.return_value = True
        self.assertFalse(launcher.consume_startup_approval(source))
        source.readline.assert_not_called()

    def test_pipe_approval_read_is_bounded(self):
        source = Mock()
        source.isatty.return_value = False
        source.readline.return_value = "x" * 64
        self.assertFalse(launcher.consume_startup_approval(source))
        source.readline.assert_called_once_with(64)

    def test_startup_approval_flag_is_restricted_to_standalone_agent(self):
        with contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as rejected:
                launcher.parse_args(["--startup-approval-stdin"])
        self.assertEqual(rejected.exception.code, 2)
        self.assertIn("only available", error.getvalue())
        options = launcher.parse_args(["--only=agent", "--startup-approval-stdin"])
        self.assertEqual(options.only, "agent")
        self.assertTrue(options.startup_approval_stdin)

    def test_compatibility_flags_and_studio_modes_are_accepted(self):
        options = launcher.parse_args(["--pwa", "--tunnel", "--screen", "--no-screen", "--imessage",
                                       "--studio=none"])
        for name in ("pwa", "tunnel", "screen", "no_screen", "imessage"):
            self.assertTrue(getattr(options, name))
        self.assertEqual(options.studio, "none")
        self.assertFalse(options.electron)
        self.assertTrue(launcher.parse_args(["--studio=electron"]).electron)
        self.assertEqual(launcher.parse_args(["--electron"]).studio, "electron")


class MessagingHelperTests(unittest.TestCase):
    def setUp(self):
        self.environment = {
            "SPECTRUM_PROJECT_ID": "private-project-id",
            "SPECTRUM_PROJECT_SECRET": "private-project-secret",
            "MESSAGING_SERVICE_TOKEN": "private-messaging-token-" + "x" * 32,
        }
        self.host = "http://127.0.0.1:18000"

    def test_sidecar_environment_preserves_node_options_without_mutating_credentials(self):
        environment = {**self.environment, "NODE_OPTIONS": "--max-old-space-size=256 --trace-warnings"}
        original = dict(environment)
        with contextlib.redirect_stderr(io.StringIO()) as output:
            extra = launcher.imessage_environment(environment, self.host)
        self.assertEqual(extra, {
            "JANUS_BACKEND_URL": self.host,
            "NODE_OPTIONS": "--max-old-space-size=256 --trace-warnings --dns-result-order=ipv4first",
        })
        self.assertEqual(environment, original)
        self.assertEqual(output.getvalue(), "")

    def test_sidecar_requires_each_credential_and_never_prints_present_secrets(self):
        for name in self.environment:
            for missing in (None, "", " \t "):
                environment = dict(self.environment)
                if missing is None:
                    del environment[name]
                else:
                    environment[name] = missing
                with self.subTest(name=name, missing=missing), contextlib.redirect_stderr(io.StringIO()) as output:
                    self.assertIsNone(launcher.imessage_environment(environment, self.host))
                self.assertIn(name, output.getvalue())
                for secret in self.environment.values():
                    self.assertNotIn(secret, output.getvalue())

    def test_messaging_token_minimum_length_is_enforced(self):
        short = {**self.environment, "MESSAGING_SERVICE_TOKEN": "s" * 31}
        with contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertIsNone(launcher.imessage_environment(short, self.host))
        self.assertIn("at least 32 characters", output.getvalue())
        self.assertNotIn("s" * 31, output.getvalue())
        accepted = {**self.environment, "MESSAGING_SERVICE_TOKEN": "s" * 32}
        self.assertEqual(launcher.imessage_environment(accepted, self.host)["NODE_OPTIONS"],
                         "--dns-result-order=ipv4first")

    def test_sidecar_down_posts_authenticated_private_body_with_a_short_timeout(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(launcher, "urlopen", return_value=response) as send:
            launcher.mark_sidecar_down(self.host, self.environment["MESSAGING_SERVICE_TOKEN"])
        request = send.call_args.args[0]
        self.assertEqual(request.full_url, self.host + "/api/messaging/heartbeat")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"connected": False})
        self.assertEqual(request.get_header("Authorization"), "Bearer " + self.environment["MESSAGING_SERVICE_TOKEN"])
        self.assertEqual(request.get_header("Content-type"), "application/json")
        self.assertNotIn(self.environment["MESSAGING_SERVICE_TOKEN"], request.full_url)
        self.assertNotIn(self.environment["MESSAGING_SERVICE_TOKEN"].encode(), request.data)
        self.assertEqual(send.call_args.kwargs, {"timeout": 2})
        response.__exit__.assert_called_once()

    def test_sidecar_down_network_and_backend_rejections_do_not_raise_or_log_secrets(self):
        errors = (OSError("fixture network unavailable"), URLError("fixture backend offline"),
                  HTTPError(self.host, 401, "fixture unauthenticated", None, None))
        for error in errors:
            with self.subTest(error=type(error).__name__), \
                    patch.object(launcher, "urlopen", side_effect=error), \
                    contextlib.redirect_stdout(io.StringIO()) as stdout, \
                    contextlib.redirect_stderr(io.StringIO()) as stderr:
                launcher.mark_sidecar_down(self.host, self.environment["MESSAGING_SERVICE_TOKEN"])
            self.assertEqual(stdout.getvalue() + stderr.getvalue(), "")


class StateDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repository"
        (self.root / "backend" / "app").mkdir(parents=True)
        self.state = Path(self.temp.name) / "private-state"

    def test_absolute_relative_and_blank_state_overrides_resolve_consistently(self):
        self.assertEqual(launcher.state_directory(self.root, {"JANUS_STATE_DIR": str(self.state)}), self.state)
        self.assertEqual(launcher.state_directory(self.root, {"JANUS_STATE_DIR": " state/session "}),
                         self.root / "state" / "session")
        self.assertEqual(launcher.state_directory(self.root, {"JANUS_STATE_DIR": " "}), self.root / ".janus")
        with patch.dict(os.environ, {"JANUS_STATE_DIR": str(self.state)}):
            self.assertEqual(launcher.state_directory(self.root), self.state)
            self.assertEqual(launcher.state_directory(self.root, {}), self.root / ".janus")

    def test_database_override_copies_legacy_rows_without_using_repository_state(self):
        legacy = self.root / "backend" / "app" / "app.db"
        with sqlite3.connect(legacy) as database:
            database.execute("CREATE TABLE retained (value TEXT)")
            database.execute("INSERT INTO retained VALUES ('existing laptop history')")
        environment = {"JANUS_STATE_DIR": str(self.state)}
        path = launcher.ensure_database(self.root, environment)
        self.assertEqual(path, self.state / "janus.sqlite")
        self.assertEqual(environment["JANUS_DB_PATH"], str(path))
        with sqlite3.connect(path) as database:
            self.assertEqual(database.execute("SELECT value FROM retained").fetchall(),
                             [("existing laptop history",)])
        self.assertFalse((self.root / ".janus").exists())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_explicit_database_path_takes_precedence_over_state_directory(self):
        environment = {"JANUS_STATE_DIR": str(self.state), "JANUS_DB_PATH": "custom.sqlite"}
        self.assertEqual(launcher.ensure_database(self.root, environment), self.root / "backend" / "custom.sqlite")
        self.assertFalse(self.state.exists())

    def test_registration_override_writes_private_credentials_and_reuses_them(self):
        environment = {"JANUS_STATE_DIR": str(self.state)}
        first = launcher.provision_registration(self.root, environment, "laptop")
        path = self.state / "devices.json"
        self.assertEqual(environment["AGENT_DEVICES_FILE"], str(path))
        self.assertEqual(json.loads(path.read_text())[0]["agent_token"], first.agent_token)
        second = launcher.provision_registration(self.root, {"JANUS_STATE_DIR": str(self.state)}, "laptop")
        self.assertEqual(second, first)
        self.assertFalse((self.root / ".janus").exists())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_owner_restriction_failure_leaves_existing_json_and_no_partial_secret_file(self):
        path = self.state / "controller.json"
        launcher.atomic_json(path, {"token": "existing-token"})
        original = path.read_bytes()
        with patch.object(launcher, "restrict_to_owner", side_effect=OSError("fixture ACL rejected")):
            with self.assertRaises(OSError):
                launcher.atomic_json(path, {"token": "new-private-token"})
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.state.iterdir()), [path])

    def test_windows_private_acl_uses_current_account_sid_and_no_inherited_grants(self):
        path = self.root / "private.json"
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher.subprocess, "check_output", return_value=" S-1-5-21-12345 \n") as identity, \
                patch.object(launcher.subprocess, "run") as apply:
            launcher.restrict_to_owner(path)
        self.assertEqual(identity.call_args.args[0][:2], ["powershell", "-NoProfile"])
        self.assertEqual(apply.call_args.args[0],
                         ["icacls", str(path), "/inheritance:r", "/grant:r", "*S-1-5-21-12345:F"])
        self.assertTrue(apply.call_args.kwargs["check"])
        self.assertEqual(apply.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(apply.call_args.kwargs["stderr"], subprocess.DEVNULL)

    def test_windows_private_acl_rejects_invalid_identity_without_applying_grants(self):
        path = self.root / "private.json"
        with patch.object(launcher.os, "name", "nt"), \
                patch.object(launcher.subprocess, "check_output", return_value="DOMAIN\\User & other-command"), \
                patch.object(launcher.subprocess, "run") as apply:
            with self.assertRaisesRegex(RuntimeError, "current Windows account"):
                launcher.restrict_to_owner(path)
        apply.assert_not_called()


class LauncherModeTests(unittest.TestCase):
    """A bounded orchestration harness, with no inherited duplicate tests."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "backend").mkdir()
        self.environment = {"AGENT_DEVICES_JSON": "[]"}
        self.registration = launcher.Registration("laptop", "owner", "a" * 40, "c" * 40)
        self.services = []
        self.output = io.StringIO()
        self.bootstrap = Mock()
        self.bootstrap.start.return_value = self.bootstrap
        self.bootstrap.browser_url.return_value = "http://127.0.0.1:5173/#private-bootstrap"

    def enable_messaging(self):
        self.environment.update(SPECTRUM_PROJECT_ID="private-project", SPECTRUM_PROJECT_SECRET="private-secret",
                                MESSAGING_SERVICE_TOKEN="m" * 40, NODE_OPTIONS="--trace-warnings")

    def run_launcher(self, arguments, *, approved=True, piped_approval=True, ready=True,
                     build_error=None, service_error=None, runtime_exit=None):
        owner = self

        class StopEvent:
            def __init__(self):
                self.waits = 0
                self.stopped = False

            def wait(self, _):
                self.waits += 1
                return self.stopped or self.waits > 2

            def set(self):
                self.stopped = True

        def make_service(name, command, cwd, env, logs_dir, **options):
            if name == service_error:
                raise OSError("fixture optional service start failure")
            service = Mock()
            service.name, service.command, service.environment = name, command, env
            service.cwd, service.logs_dir, service.options = cwd, logs_dir, options
            service.process = SimpleNamespace(pid=1000 + len(owner.services))
            service.start.return_value = service
            service.poll.return_value = runtime_exit.get(name) if runtime_exit else None
            owner.services.append(service)
            return service

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(launcher, "ROOT", self.root))
            stack.enter_context(patch.object(launcher, "load_environment", return_value=dict(self.environment)))
            self.install = stack.enter_context(patch.object(launcher, "install_dependencies"))
            self.ports = stack.enter_context(patch.object(launcher, "check_ports"))
            self.database = stack.enter_context(patch.object(launcher, "ensure_database", return_value=self.root / ".janus" / "janus.sqlite"))
            stack.enter_context(patch.object(launcher, "provision_registration", return_value=self.registration))
            stack.enter_context(patch.object(launcher.platform, "system", return_value="Darwin"))
            self.approval = stack.enter_context(patch.object(launcher, "approve_startup", return_value=approved))
            self.piped_approval = stack.enter_context(patch.object(launcher, "consume_startup_approval", return_value=piped_approval))
            self.policy = stack.enter_context(patch.object(launcher, "ensure_policy", return_value=self.root / "agent" / ".validation-policy.json"))
            self.write_state = stack.enter_context(patch.object(launcher, "atomic_json", wraps=launcher.atomic_json))
            stack.enter_context(patch.object(launcher, "ManagedProcess", side_effect=make_service))
            self.bootstrap_factory = stack.enter_context(patch.object(launcher, "LocalBootstrap", return_value=self.bootstrap))
            self.cloudflare = stack.enter_context(patch.object(launcher, "ensure_cloudflared"))
            stack.enter_context(patch.object(launcher, "threading", SimpleNamespace(Event=StopEvent)))
            stack.enter_context(patch.object(launcher.signal, "getsignal", return_value=None))
            stack.enter_context(patch.object(launcher.signal, "signal"))
            self.http = stack.enter_context(patch.object(launcher, "wait_http", return_value=ready))
            self.browser = stack.enter_context(patch.object(launcher.webbrowser, "open"))
            self.build = stack.enter_context(patch.object(launcher.subprocess, "run", side_effect=build_error))
            stack.enter_context(patch.object(launcher.shutil, "which", return_value="fixture-npm"))
            self.down = stack.enter_context(patch.object(launcher, "mark_sidecar_down"))
            with contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
                return launcher.main(arguments)

    def assert_local_services_cleaned_up(self):
        for service in self.services:
            with self.subTest(service=service.name):
                service.stop.assert_called_once()

    def test_default_launch_opens_electron_with_the_selected_private_connection(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent", "--backend-port=18000",
                                           "--studio-port=15173"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa", "electron"])
        self.assertEqual(self.install.call_args.args[2:], (True, False))
        electron = self.services[-1]
        self.assertEqual(electron.command, ["node", str(self.root / "frontend" / "electron" / "launch.cjs")])
        self.assertEqual(electron.cwd, self.root / "frontend")
        self.assertEqual(electron.environment["JANUS_STUDIO_URL"], self.bootstrap.browser_url())
        self.assertEqual(electron.environment["JANUS_BACKEND_URL"], "http://127.0.0.1:18000")
        self.assertEqual(electron.environment["AGENT_DEVICE_ID"], self.registration.device_id)
        self.bootstrap_factory.assert_called_once_with("http://127.0.0.1:15173", "http://127.0.0.1:18000",
                                                       self.registration.controller_token)
        self.browser.assert_not_called()
        markers = [line for line in self.output.getvalue().splitlines() if line.startswith("[janus-process]")]
        self.assertEqual(markers, [f"[janus-process] {service.name} {service.process.pid}" for service in self.services])
        for secret in (self.registration.agent_token, self.registration.controller_token):
            self.assertNotIn(secret, " ".join(electron.command))
            self.assertNotIn(secret, self.output.getvalue())
        self.assert_local_services_cleaned_up()

    def test_explicit_browser_uses_bootstrap_without_installing_or_starting_electron(self):
        self.assertEqual(self.run_launcher(["--studio=browser", "--local-only", "--no-agent"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.assertEqual(self.install.call_args.args[2:], (False, False))
        self.browser.assert_called_once_with(self.bootstrap.browser_url())
        self.assert_local_services_cleaned_up()

    def test_headless_default_serves_studio_and_pwa_without_electron_runtime_or_window(self):
        self.assertEqual(self.run_launcher(["--no-browser", "--local-only", "--no-agent"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.assertEqual(self.install.call_args.args[2:], (False, False))
        self.assertEqual([call.args[0] for call in self.http.call_args_list],
                         ["http://127.0.0.1:8000/health", "http://127.0.0.1:5173", "http://127.0.0.1:5174"])
        self.bootstrap.start.assert_called_once()
        self.browser.assert_not_called()
        self.assertNotIn("[janus-process] electron", self.output.getvalue())
        self.assert_local_services_cleaned_up()

    def test_default_electron_waits_for_all_services_to_be_ready(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent"], ready=False), 1)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.bootstrap_factory.assert_not_called()
        self.browser.assert_not_called()
        self.assertNotIn("[janus-process] electron", self.output.getvalue())
        self.assert_local_services_cleaned_up()

    def test_closing_default_electron_stops_all_local_services(self):
        self.assertEqual(self.run_launcher(["--local-only", "--no-agent"], runtime_exit={"electron": 0}), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa", "electron"])
        self.bootstrap.stop.assert_called_once()
        self.assert_local_services_cleaned_up()

    def test_solo_agent_preserves_main_public_origins_and_controller_credentials(self):
        state = self.root / ".janus"
        state.mkdir()
        origins = state / "public-urls.json"
        controller = state / "controller.json"
        origins.write_text('{"backend":"https://active.example","pwa":"https://phone.example"}\n')
        controller.write_text('{"token":"main-controller-token"}\n')
        original = origins.read_bytes(), controller.read_bytes()
        self.assertEqual(self.run_launcher(["--only=agent", "--no-install"]), 0)
        self.assertEqual([service.name for service in self.services], ["agent"])
        self.assertEqual(self.install.call_args.args[2:], (False, True))
        markers = [line for line in self.output.getvalue().splitlines() if line.startswith("[janus-process]")]
        self.assertEqual(markers, ["[janus-process] agent 1000"])
        self.ports.assert_not_called()
        self.database.assert_not_called()
        self.write_state.assert_not_called()
        self.bootstrap_factory.assert_not_called()
        self.browser.assert_not_called()
        self.cloudflare.assert_not_called()
        self.assertEqual((origins.read_bytes(), controller.read_bytes()), original)
        self.assert_local_services_cleaned_up()

    def test_solo_agent_desktop_pipe_approval_bypasses_terminal_prompt(self):
        self.assertEqual(self.run_launcher(["--only=agent", "--startup-approval-stdin", "--no-install"]), 0)
        self.piped_approval.assert_called_once()
        self.approval.assert_not_called()
        agent = self.services[0]
        self.assertEqual(agent.options["input_data"], "JANUS_SESSION_APPROVED\n")
        self.assertIn("--session-approval-stdin", agent.command)
        self.assertEqual(agent.environment["AGENT_TOKEN"], self.registration.agent_token)
        self.assertNotIn(self.registration.agent_token, " ".join(agent.command))

    def test_solo_agent_denied_pipe_permission_never_starts_or_creates_policy(self):
        self.assertEqual(self.run_launcher(["--only=agent", "--startup-approval-stdin", "--no-install"],
                                           piped_approval=False), 1)
        self.assertFalse(self.services)
        self.policy.assert_not_called()
        self.http.assert_not_called()
        self.write_state.assert_not_called()

    def test_solo_agent_requires_existing_backend_and_cleans_up_without_public_state(self):
        self.assertEqual(self.run_launcher(["--only=agent", "--backend-port=18000", "--no-install"], ready=False), 1)
        self.http.assert_called_once_with("http://127.0.0.1:18000/health", 35)
        self.assertFalse(self.services)
        self.write_state.assert_not_called()
        self.assertIn("Start the JANUS backend", self.output.getvalue())

    def test_solo_agent_exit_propagates_and_stops_its_process(self):
        self.assertEqual(self.run_launcher(["--only=agent", "--no-install"], runtime_exit={"agent": 7}), 7)
        self.assert_local_services_cleaned_up()
        self.write_state.assert_not_called()

    def test_no_screen_keeps_other_native_controls_and_private_session_approval(self):
        self.assertEqual(self.run_launcher(["--only=agent", "--screen", "--no-screen", "--no-install"]), 0)
        agent = self.services[0]
        self.assertNotIn("--screen", agent.command)
        self.assertIn("--native-tools", agent.command)
        self.assertIn("--validation-policy", agent.command)
        self.assertEqual(agent.options["input_data"], "JANUS_SESSION_APPROVED\n")

    def test_studio_none_skips_studio_browser_and_bootstrap_but_keeps_phone_pwa(self):
        self.assertEqual(self.run_launcher(["--studio=none", "--no-agent", "--local-only", "--no-install"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "pwa"])
        self.assertEqual(self.install.call_args.args[2:], (False, True))
        self.assertEqual([call.args[0] for call in self.http.call_args_list],
                         ["http://127.0.0.1:8000/health", "http://127.0.0.1:5174"])
        self.bootstrap_factory.assert_not_called()
        self.browser.assert_not_called()
        self.assert_local_services_cleaned_up()

    def test_custom_state_directory_applies_to_controller_public_origins_and_logs(self):
        self.environment["JANUS_STATE_DIR"] = "private-session"
        state = self.root / "private-session"
        self.assertEqual(self.run_launcher(["--studio=none", "--no-agent", "--local-only", "--no-install"]), 0)
        self.assertTrue((state / "controller.json").is_file())
        self.assertEqual(json.loads((state / "public-urls.json").read_text()), {"backend": None, "pwa": None})
        self.assertFalse((self.root / ".janus").exists())
        self.assertTrue(all(service.logs_dir == state / "logs" for service in self.services))
        self.assertTrue(all(service.environment["JANUS_PUBLIC_URLS_FILE"] == str(state / "public-urls.json")
                            for service in self.services))

    def test_configured_imessage_builds_and_receives_ipv4_options_and_redacted_secrets(self):
        self.enable_messaging()
        self.assertEqual(self.run_launcher(["--imessage", "--no-agent", "--local-only", "--no-install", "--no-browser"]), 0)
        sidecar = next(service for service in self.services if service.name == "imessage")
        self.assertEqual(sidecar.command, ["node", "dist/index.js"])
        self.assertEqual(sidecar.cwd, self.root / "imessage")
        self.assertEqual(sidecar.environment["JANUS_BACKEND_URL"], "http://127.0.0.1:8000")
        self.assertEqual(sidecar.environment["NODE_OPTIONS"], "--trace-warnings --dns-result-order=ipv4first")
        self.assertIn("@janus/imessage", self.build.call_args.args[0])
        for name in ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET", "MESSAGING_SERVICE_TOKEN"):
            self.assertIn(self.environment[name], sidecar.options["redactions"])
            self.assertNotIn(self.environment[name], self.output.getvalue())
        self.down.assert_called_once_with("http://127.0.0.1:8000", self.environment["MESSAGING_SERVICE_TOKEN"])
        self.assert_local_services_cleaned_up()

    def test_missing_imessage_settings_skip_sidecar_without_stopping_local_services(self):
        self.assertEqual(self.run_launcher(["--imessage", "--no-agent", "--local-only", "--no-install", "--no-browser"]), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.build.assert_not_called()
        self.assertIn("iMessage sidecar not started", self.output.getvalue())
        self.assert_local_services_cleaned_up()

    def test_imessage_build_failure_keeps_local_services_and_reports_sidecar_down(self):
        self.enable_messaging()
        failure = subprocess.CalledProcessError(1, ["fixture build"])
        self.assertEqual(self.run_launcher(["--imessage", "--no-agent", "--local-only", "--no-install", "--no-browser"],
                                           build_error=failure), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.assertIn("iMessage build/start failed", self.output.getvalue())
        self.down.assert_called_with("http://127.0.0.1:8000", self.environment["MESSAGING_SERVICE_TOKEN"])
        self.assert_local_services_cleaned_up()

    def test_imessage_start_failure_keeps_local_services_and_reports_sidecar_down(self):
        self.enable_messaging()
        self.assertEqual(self.run_launcher(["--imessage", "--no-agent", "--local-only", "--no-install", "--no-browser"],
                                           service_error="imessage"), 0)
        self.assertEqual([service.name for service in self.services], ["backend", "studio", "pwa"])
        self.assertIn("iMessage build/start failed", self.output.getvalue())
        self.down.assert_called_with("http://127.0.0.1:8000", self.environment["MESSAGING_SERVICE_TOKEN"])
        self.assert_local_services_cleaned_up()

    def test_crashed_imessage_is_removed_reported_down_and_does_not_kill_stack(self):
        self.enable_messaging()
        self.assertEqual(self.run_launcher(["--imessage", "--no-agent", "--local-only", "--no-install", "--no-browser"],
                                           runtime_exit={"imessage": 1}), 0)
        sidecar = next(service for service in self.services if service.name == "imessage")
        sidecar.poll.assert_called_once()
        sidecar.stop.assert_called_once()
        self.assertEqual(self.down.call_count, 2)  # Crash notification plus final session teardown.
        self.assertIn("iMessage sidecar stopped", self.output.getvalue())
        self.assertIn("Local JANUS remains available", self.output.getvalue())
        self.assert_local_services_cleaned_up()


if __name__ == "__main__":
    unittest.main()
