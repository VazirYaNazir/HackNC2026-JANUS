"""Public-origin probe diagnostics and verified HTTPS trust regressions.

All failures are injected locally. No Cloudflare endpoint, user trust store,
credentials, or desktop permissions are changed by these tests.
"""
import importlib.util
import hashlib
import io
import json
from pathlib import Path
import socket
import ssl
import sys
import tarfile
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("janus_resilience_launcher", ROOT / "start.py")
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)

PUBLIC_URL = "https://fixture.trycloudflare.com/health"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        # Advance past the test deadline after one failure without wall-clock waits.
        self.now += 10


def response(status=200):
    opened = MagicMock()
    opened.__enter__.return_value.status = status
    return opened


class PublicProbeDiagnosticsTests(unittest.TestCase):
    def failed_probe(self, failure):
        clock, diagnostics = FakeClock(), {}
        with patch.object(launcher.time, "monotonic", clock), patch.object(launcher.time, "sleep", clock.sleep), patch.object(launcher, "urlopen", side_effect=failure) as request:
            self.assertFalse(launcher.wait_http(PUBLIC_URL, 1, diagnostics=diagnostics))
        request.assert_called_once()
        self.assertTrue(diagnostics.get("error"), "Failed probes must retain an actionable diagnostic")
        return diagnostics["error"].lower()

    def test_http_error_reports_status_instead_of_a_generic_cloudflare_failure(self):
        for status in (403, 429, 502, 503, 530):
            with self.subTest(status=status):
                error = self.failed_probe(HTTPError(PUBLIC_URL, status, "fixture error", {}, None))
                self.assertIn("http", error)
                self.assertIn(str(status), error)

    def test_wrapped_certificate_error_identifies_tls_trust_failure(self):
        failure = URLError(ssl.SSLCertVerificationError(1, "certificate verify failed"))
        error = self.failed_probe(failure)
        self.assertTrue("tls" in error or "certificate" in error, error)

    def test_direct_tls_error_is_classified_without_disabling_certificate_validation(self):
        error = self.failed_probe(ssl.SSLError(1, "TLS handshake failed"))
        self.assertIn("tls", error)

    def test_dns_error_identifies_hostname_resolution(self):
        error = self.failed_probe(URLError(socket.gaierror(-2, "Name or service not known")))
        self.assertIn("dns", error)

    def test_connectivity_and_timeout_errors_are_distinguishable_from_dns_and_tls(self):
        for failure in (URLError(ConnectionRefusedError("Connection refused")), URLError(TimeoutError("timed out")), TimeoutError("timed out")):
            with self.subTest(error_type=type(failure).__name__):
                error = self.failed_probe(failure)
                self.assertTrue(any(term in error for term in ("connect", "timeout", "timed out", "network")), error)
                self.assertNotIn("certificate", error)

    def test_connected_public_probe_receives_a_verified_context_and_clears_old_error(self):
        context, diagnostics = Mock(), {"error": "previous DNS failure"}
        with patch.object(launcher, "verified_tls_context", return_value=context) as trust, patch.object(launcher, "urlopen", return_value=response()) as request:
            self.assertTrue(launcher.wait_http(PUBLIC_URL, 1, diagnostics=diagnostics))
        trust.assert_called_once()
        self.assertIs(request.call_args.kwargs.get("context"), context)
        self.assertFalse(diagnostics.get("error"), "Recovered probes must clear stale errors")

    def test_local_http_readiness_needs_no_https_trust_store(self):
        with patch.object(launcher, "verified_tls_context") as trust, patch.object(launcher, "urlopen", return_value=response()):
            self.assertTrue(launcher.wait_http("http://127.0.0.1:8000/health", 1))
        trust.assert_not_called()

    def test_exited_local_service_is_reported_without_attempting_network_access(self):
        diagnostics = {}
        process = Mock()
        process.poll.return_value = 1
        with patch.object(launcher, "urlopen") as request:
            self.assertFalse(launcher.wait_http(PUBLIC_URL, 1, process, diagnostics=diagnostics))
        request.assert_not_called()
        self.assertTrue(diagnostics.get("error"), "An exited service needs a useful startup diagnostic")

    def test_already_cancelled_probe_returns_without_contacting_the_public_origin(self):
        cancelled, diagnostics = threading.Event(), {}
        cancelled.set()
        with patch.object(launcher, "urlopen") as request:
            self.assertFalse(launcher.wait_http(PUBLIC_URL, 35, diagnostics=diagnostics, cancelled=cancelled))
        request.assert_not_called()
        self.assertEqual(diagnostics.get("error"), "HTTP readiness check cancelled")

    def test_cancellation_between_retries_prevents_a_second_public_request(self):
        cancelled, diagnostics, clock = threading.Event(), {}, FakeClock()
        def cancel_during_delay(seconds):
            cancelled.set()
            # Stay inside the readiness deadline so cancellation, rather than
            # deadline expiry, must stop the next attempt.
            clock.now += 0.1
        with patch.object(launcher.time, "monotonic", clock), patch.object(launcher.time, "sleep", cancel_during_delay), patch.object(launcher, "urlopen", side_effect=URLError(TimeoutError("fixture timeout"))) as request:
            self.assertFalse(launcher.wait_http(PUBLIC_URL, 1, diagnostics=diagnostics, cancelled=cancelled))
        request.assert_called_once()
        self.assertEqual(diagnostics.get("error"), "HTTP readiness check cancelled")


class VerifiedTrustContextTests(unittest.TestCase):
    def test_real_context_requires_valid_chain_and_hostname_and_has_trusted_roots(self):
        context = launcher.verified_tls_context()
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        self.assertTrue(context.get_ca_certs(), "HTTPS trust must include real certificate roots")

    def test_certifi_roots_are_added_to_default_system_roots(self):
        context = Mock(spec=ssl.SSLContext)
        context.verify_mode = ssl.CERT_REQUIRED
        context.check_hostname = True
        bundle = "/fixture/ca-bundle.pem"
        certifi = Mock()
        certifi.where.return_value = bundle
        with patch.object(launcher.ssl, "create_default_context", return_value=context) as defaults, patch.dict(sys.modules, {"certifi": certifi}):
            self.assertIs(launcher.verified_tls_context(), context)
        defaults.assert_called_once()
        context.load_verify_locations.assert_called_once()
        arguments = context.load_verify_locations.call_args
        self.assertEqual(arguments.kwargs.get("cafile") or arguments.args[0], bundle)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_optional_certifi_absence_keeps_system_verification_enabled(self):
        with patch.dict(sys.modules, {"certifi": None}):
            context = launcher.verified_tls_context()
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class InstallerTrustTests(unittest.TestCase):
    def test_official_release_metadata_and_macos_binary_download_both_verify_https(self):
        binary = b"fixture macOS arm64 cloudflared"
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w:gz") as package:
            member = tarfile.TarInfo("cloudflared")
            member.size = len(binary)
            package.addfile(member, io.BytesIO(binary))
        payload = archive.getvalue()
        release = {"tag_name": "v2026", "assets": [{
            "name": "cloudflared-darwin-arm64.tgz",
            "digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "browser_download_url": "https://github.com/cloudflare/cloudflared/releases/download/v2026/fixture",
        }]}
        trust = launcher.verified_tls_context()
        responses = [io.BytesIO(json.dumps(release).encode()), io.BytesIO(payload)]
        with tempfile.TemporaryDirectory() as temporary, patch.object(launcher.shutil, "which", return_value=None), patch.object(launcher.platform, "system", return_value="Darwin"), patch.object(launcher.platform, "machine", return_value="arm64"), patch.object(launcher, "verified_tls_context", return_value=trust), patch.object(launcher, "urlopen", side_effect=responses) as download:
            installed = Path(launcher.ensure_cloudflared(Path(temporary), None))
            self.assertEqual(installed.read_bytes(), binary)
        self.assertEqual(download.call_count, 2)
        for call in download.call_args_list:
            self.assertIs(call.kwargs.get("context"), trust)
            self.assertTrue(call.args[0].full_url.startswith("https://"))
        self.assertEqual(trust.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(trust.check_hostname)


if __name__ == "__main__":
    unittest.main()
