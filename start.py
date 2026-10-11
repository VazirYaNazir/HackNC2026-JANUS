#!/usr/bin/env python3
"""Start JANUS from the repository root: npm start -- [options]."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import threading
import time
from dataclasses import dataclass
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
TUNNEL_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[\s/\"'<>]|$)", re.I)


def state_directory(root: Path, env: dict[str, str] | None = None) -> Path:
    configured = (os.environ if env is None else env).get("JANUS_STATE_DIR", "").strip()
    path = Path(configured) if configured else root / ".janus"
    return path if path.is_absolute() else root / path


def restrict_to_owner(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o600)
        return
    identity = subprocess.check_output(["powershell", "-NoProfile", "-Command",
        "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"], text=True).strip()
    if not re.fullmatch(r"S-1-[0-9-]+", identity):
        raise RuntimeError("Could not identify the current Windows account for private JANUS files")
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{identity}:F"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def atomic_json(path: Path, data: dict | list) -> None:
    """Write private local state without exposing partially written JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            restrict_to_owner(temporary)
            json.dump(data, file, indent=2)
            file.write("\n")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_environment(path: Path, environ: dict[str, str]) -> dict[str, str]:
    from dotenv import dotenv_values
    values = dotenv_values(path, interpolate=False) if path.exists() else {}
    return {**{name: value for name, value in values.items() if value is not None}, **environ}


def ensure_database(root: Path, env: dict[str, str]) -> Path:
    """Keep launcher state writable and retain an existing database on first use."""
    configured = env.get("JANUS_DB_PATH", "").strip()
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = root / "backend" / path
        if not path.parent.is_dir():
            raise ValueError("JANUS_DB_PATH must have an existing parent directory")
        if path.exists() and not os.access(path, os.W_OK):
            raise ValueError("Configured JANUS_DB_PATH is read-only; choose a writable database path")
    else:
        path = state_directory(root, env) / "janus.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        legacy = root / "backend" / "app" / "app.db"
        if not path.exists():
            temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(descriptor)
            try:
                restrict_to_owner(temporary)
                if legacy.exists():
                    with closing(sqlite3.connect(legacy.resolve().as_uri() + "?mode=ro", uri=True)) as original:
                        with closing(sqlite3.connect(temporary)) as destination:
                            original.backup(destination)
                            destination.commit()
                os.replace(temporary, path)
            except sqlite3.Error:
                raise ValueError("Could not copy the existing database; the original is unchanged") from None
            finally:
                temporary.unlink(missing_ok=True)
                for suffix in ("-journal", "-wal", "-shm"):
                    Path(str(temporary) + suffix).unlink(missing_ok=True)
    env["JANUS_DB_PATH"] = str(path)
    return path


@dataclass(frozen=True)
class Registration:
    device_id: str
    owner_id: str
    agent_token: str
    controller_token: str


def provision_registration(root: Path, env: dict[str, str], device_id: str | None) -> Registration:
    """Reuse configured credentials; generate distinct roles only for a new setup."""
    configured_file = env.get("AGENT_DEVICES_FILE", "").strip()
    raw = env.get("AGENT_DEVICES_JSON", "[]").strip() or "[]"
    state_file = state_directory(root, env) / "devices.json"
    if configured_file:
        path = Path(configured_file)
        if not path.is_absolute():
            path = root / "backend" / path
        if path.stat().st_size > 65536:
            raise ValueError("Registration file exceeds 64 KiB")
        raw = path.read_text(encoding="utf-8")
    elif raw == "[]" and state_file.exists():
        raw = state_file.read_text(encoding="utf-8")
    try:
        entries = json.loads(raw)
        if not isinstance(entries, list) or len(entries) > 128 or len(raw.encode()) > 65536:
            raise ValueError
        registrations, used_devices, used_tokens = [], set(), set()
        for item in entries:
            if not isinstance(item, dict) or set(item) != {"device_id", "owner_id", "agent_token", "controller_token"}:
                raise ValueError
            registration = Registration(**item)
            if not all(isinstance(value, str) and IDENTIFIER.fullmatch(value) for value in (registration.device_id, registration.owner_id)):
                raise ValueError
            if registration.device_id in used_devices:
                raise ValueError
            used_devices.add(registration.device_id)
            for token in (registration.agent_token, registration.controller_token):
                if not isinstance(token, str) or not 32 <= len(token) <= 256 or token in used_tokens:
                    raise ValueError
                used_tokens.add(token)
            registrations.append(registration)
    except (ValueError, TypeError):
        raise ValueError("Invalid device registrations: IDs and distinct credentials must match the backend schema") from None
    if not registrations:
        if configured_file:
            raise ValueError("Configured registration file is empty; add a device to that file")
        selected_id = device_id or "my-laptop"
        if not IDENTIFIER.fullmatch(selected_id):
            raise ValueError("Invalid device ID")
        registration = Registration(selected_id, "local-owner", secrets.token_urlsafe(48), secrets.token_urlsafe(48))
        atomic_json(state_file, [registration.__dict__])
        env["AGENT_DEVICES_FILE"] = str(state_file)
        return registration
    if not configured_file and state_file.exists() and raw == state_file.read_text(encoding="utf-8"):
        env["AGENT_DEVICES_FILE"] = str(state_file)
    if device_id:
        chosen = next((item for item in registrations if item.device_id == device_id), None)
        if chosen is None:
            raise ValueError("Requested device ID is not registered")
        return chosen
    if len(registrations) != 1:
        raise ValueError("Multiple laptops are registered; select one with --device-id")
    return registrations[0]


def validate_origin(url: str) -> str:
    try:
        if any(character.isspace() for character in url) or "\\" in url:
            raise ValueError
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment
                or parsed.hostname in ("localhost", "127.0.0.1", "::1")
                or any(character.isspace() for character in parsed.netloc)):
            raise ValueError
        parsed.port
    except ValueError:
        raise ValueError("Public phone addresses must be HTTPS origins without paths, credentials, or query parameters") from None
    return f"https://{parsed.netloc}"


def extract_tunnel_url(line: str) -> str | None:
    match = TUNNEL_URL.search(line)
    if match and urlsplit(match.group()).hostname != "api.trycloudflare.com":
        return validate_origin(match.group())
    return None


def approve_startup(input_stream=None, input_fn=input, output_stream=None) -> bool:
    source, destination = input_stream or sys.stdin, output_stream or sys.stdout
    if not source.isatty():
        print("Laptop permissions require an interactive terminal. Use --no-agent for web development.", file=destination)
        return False
    print("\nAllow JANUS controls and paired phones to use this laptop for this session?", file=destination)
    print("This enables mouse/volume control, screen sharing, process inspection, and generated jobs", file=destination)
    print("Optional iMessage commands can also lock the desktop.", file=destination)
    print("with file read/write, network/external sending, process spawning, admin and persistent-write", file=destination)
    print("capabilities within the local policy and isolation worker. Job outputs may be shared.", file=destination)
    print("Generated jobs still require an installed isolation adapter. Approval ends when JANUS exits.", file=destination)
    try:
        answer = input_fn("Type yes to approve, or press Enter to decline: ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() == "yes"


def consume_startup_approval(input_stream=None) -> bool:
    """Receive the local desktop dialog's approval through a private pipe."""
    source = input_stream or sys.stdin
    return not source.isatty() and source.readline(64) == "JANUS_STARTUP_APPROVED\n"


def mark_sidecar_down(host: str, token: str) -> None:
    try:
        request = Request(host + "/api/messaging/heartbeat", data=b'{"connected":false}',
                          headers={"Content-Type": "application/json", "Authorization": "Bearer " + token}, method="POST")
        with urlopen(request, timeout=2):
            pass
    except (OSError, URLError):
        pass


def imessage_environment(env: dict[str, str], host: str) -> dict[str, str] | None:
    required = ("SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET", "MESSAGING_SERVICE_TOKEN")
    missing = [name for name in required if not env.get(name, "").strip()]
    if missing:
        print("iMessage sidecar not started: set " + ", ".join(missing) + " in backend/.env.", file=sys.stderr)
        return None
    if len(env["MESSAGING_SERVICE_TOKEN"]) < 32:
        print("iMessage sidecar not started: MESSAGING_SERVICE_TOKEN must contain at least 32 characters.", file=sys.stderr)
        return None
    return {"JANUS_BACKEND_URL": host, "NODE_OPTIONS": (env.get("NODE_OPTIONS", "") + " --dns-result-order=ipv4first").strip()}


def check_ports(ports: list[int]) -> None:
    if len(set(ports)) != len(ports) or any(not 1 <= port <= 65535 for port in ports):
        raise ValueError("Service ports must be distinct numbers between 1 and 65535")
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name != "nt":
                # Uvicorn uses SO_REUSEADDR; match it so a stopped service's
                # TIME_WAIT sockets do not prevent an immediate restart.
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise ValueError(f"Port {port} is already in use; stop its service or choose another port") from None


def verified_tls_context() -> ssl.SSLContext:
    """Retain system trust and add public roots for managed macOS Python builds."""
    context = ssl.create_default_context()
    try:
        import certifi
    except ImportError:
        pass
    else:
        context.load_verify_locations(cafile=certifi.where())
    return context


def http_probe_error(error: Exception) -> str:
    """Describe failures without echoing credential-bearing URLs or proxy settings."""
    if isinstance(error, HTTPError):
        return f"HTTP {error.code} from the public service"
    reason = error.reason if isinstance(error, URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return "TLS certificate verification failed; check the Python trust store or configured CA bundle"
    if isinstance(reason, ssl.SSLError):
        return "TLS handshake failed"
    if isinstance(reason, socket.gaierror):
        return "DNS lookup failed; the new tunnel hostname may still be propagating"
    if isinstance(reason, TimeoutError):
        return "Network connection timed out"
    if isinstance(reason, ConnectionRefusedError):
        return "Network connection refused"
    return "Network connection failed; check connectivity and HTTPS proxy settings"


def wait_http(url: str, timeout: float, process=None, *, diagnostics: dict | None = None, cancelled=None) -> bool:
    deadline = time.monotonic() + timeout
    context = None
    last_error = "HTTP readiness check timed out"
    while time.monotonic() < deadline:
        if cancelled is not None and cancelled.is_set():
            if diagnostics is not None:
                diagnostics["error"] = "HTTP readiness check cancelled"
            return False
        if process is not None and process.poll() is not None:
            if diagnostics is not None:
                diagnostics["error"] = "Service exited before its HTTP readiness check completed"
            return False
        try:
            kwargs = {}
            if urlsplit(url).scheme == "https":
                context = context or verified_tls_context()
                kwargs["context"] = context
            with urlopen(Request(url, headers={"User-Agent": "JANUS-start"}), timeout=min(2, max(0.1, deadline - time.monotonic())), **kwargs) as response:
                if response.status == 200:
                    if diagnostics is not None:
                        diagnostics.pop("error", None)
                    return True
                last_error = f"HTTP {response.status} from the public service"
        except (OSError, URLError, TimeoutError) as error:
            last_error = http_probe_error(error)
        time.sleep(min(0.15, max(0, deadline - time.monotonic())))
    if diagnostics is not None:
        diagnostics["error"] = last_error
    return False


class ManagedProcess:
    """Own a service process tree and continuously drain/redact its output."""
    def __init__(self, name, command, cwd, env, logs_dir, redactions=(), on_line=None, input_data=None, console=False):
        self.name, self.command, self.cwd, self.env = name, command, cwd, env
        self.logs_dir, self.redactions = Path(logs_dir), tuple(value for value in redactions if value)
        self.on_line, self.input_data, self.console = on_line, input_data, console
        self.process = None
        self.reader = None

    def start(self):
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        log = self.logs_dir / f"{self.name}.log"
        descriptor = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        file = os.fdopen(descriptor, "w", encoding="utf-8")
        try:
            restrict_to_owner(log)
        except BaseException:
            file.close()
            raise
        kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
        try:
            self.process = subprocess.Popen(self.command, cwd=self.cwd, env=self.env, text=True, encoding="utf-8", errors="replace",
                                            stdin=subprocess.PIPE if self.input_data is not None else subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        except BaseException:
            file.close()
            raise

        def drain():
            try:
                for line in self.process.stdout:
                    for value in self.redactions:
                        line = line.replace(value, "[REDACTED]")
                    file.write(line)
                    file.flush()
                    if self.console:
                        print(f"[{self.name}] {line.rstrip()}")
                    if self.on_line:
                        self.on_line(line)
            finally:
                file.close()
                self.process.stdout.close()

        self.reader = threading.Thread(target=drain, name=f"janus:{self.name}", daemon=True)
        self.reader.start()
        if self.input_data is not None:
            try:
                self.process.stdin.write(self.input_data)
                self.process.stdin.flush()
            except BrokenPipeError:
                pass
            finally:
                self.process.stdin.close()
        return self

    def poll(self):
        return self.process.poll() if self.process else None

    def stop(self):
        if self.process is None:
            return
        if os.name == "nt":
            if self.process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.process.wait(timeout=5)
        if self.reader:
            if os.name != "nt":
                # A child can outlive its exited parent and keep the pipe open.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            self.reader.join(timeout=2)


class CloudflareTunnel:
    def __init__(self, name, executable, port, env, logs_dir):
        self.name, self.executable, self.port = name, executable, port
        self.env, self.logs_dir = env, logs_dir
        self.url = None
        self.ready = threading.Event()
        self.service = None
        self.last_error = ""
        self.cancelled = None

    def start(self):
        if self.cancelled is not None and self.cancelled.is_set():
            raise RuntimeError(f"{self.name} connection cancelled")
        self.url = None
        self.ready.clear()
        self.last_error = ""
        candidate = None
        connected = False

        def capture(line):
            nonlocal candidate, connected
            url = extract_tunnel_url(line)
            if url:
                candidate = url
            if "Registered tunnel connection" in line:
                connected = True
            if "failed" in line.lower() or " ERR " in line:
                self.last_error = line.strip()[:2048]
            if candidate and connected:
                self.url = candidate
                self.ready.set()

        self.service = ManagedProcess(self.name, [str(self.executable), "tunnel", "--no-autoupdate", "--protocol", "http2", "--url", f"http://127.0.0.1:{self.port}"],
                                      ROOT, self.env, self.logs_dir, on_line=capture).start()
        pid = getattr(getattr(self.service, "process", None), "pid", None)
        if isinstance(pid, int):
            print(f"[janus-process] {self.name} {pid}", flush=True)
        deadline = time.monotonic() + 40
        while not self.ready.wait(timeout=0.2):
            if self.cancelled is not None and self.cancelled.is_set():
                self.stop()
                raise RuntimeError(f"{self.name} connection cancelled")
            if self.service.poll() is not None or time.monotonic() >= deadline:
                self.stop()
                detail = f" {self.last_error}" if self.last_error else ""
                raise RuntimeError(f"{self.name} did not establish a Cloudflare connection.{detail} See {self.logs_dir / (self.name + '.log')}")
        return self.url

    def stop(self):
        if self.service:
            self.service.stop()
        self.url = None


class LocalBootstrap:
    """Single-use, origin-restricted loopback credential handoff to Studio."""
    def __init__(self, studio_origin: str, host: str, token: str):
        self.studio_origin, self.host, self.token = studio_origin.rstrip("/"), host, token
        self.nonce = secrets.token_urlsafe(32)
        self.expires = time.monotonic() + 180
        self.consumed = False
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body=None):
                self.send_response(status)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Type", "application/json")
                if self.headers.get("Origin") == owner.studio_origin:
                    self.send_header("Access-Control-Allow-Origin", owner.studio_origin)
                    self.send_header("Vary", "Origin")
                self.end_headers()
                self.wfile.write(json.dumps(body or {}).encode())

            def do_OPTIONS(self):
                if self.path != "/bootstrap" or self.headers.get("Origin") != owner.studio_origin:
                    self.reply(403)
                    return
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", owner.studio_origin)
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()

            def do_POST(self):
                if self.path != "/bootstrap" or self.headers.get("Origin") != owner.studio_origin:
                    self.reply(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError
                    payload = json.loads(self.rfile.read(length))
                    if not isinstance(payload, dict) or set(payload) != {"nonce"} or not isinstance(payload["nonce"], str):
                        raise ValueError
                except (ValueError, UnicodeError):
                    self.reply(400)
                    return
                with owner.lock:
                    if owner.consumed or time.monotonic() > owner.expires:
                        self.reply(410)
                    elif not secrets.compare_digest(payload["nonce"], owner.nonce):
                        self.reply(403)
                    else:
                        owner.consumed = True
                        self.reply(200, {"host": owner.host, "token": owner.token})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = None

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def browser_url(self):
        return f"{self.studio_origin}/#{urlencode({'janus-start': self.origin, 'nonce': self.nonce})}"

    def start(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=2)
        self.server.server_close()


def ensure_cloudflared(root: Path, explicit: str | None) -> str:
    executable = explicit or shutil.which("cloudflared")
    if executable:
        if not Path(executable).is_file():
            raise ValueError("Configured cloudflared executable does not exist")
        return str(executable)
    system = {"Darwin": "darwin", "Windows": "windows", "Linux": "linux"}.get(platform.system())
    architecture = {"x86_64": "amd64", "AMD64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine())
    if not system or not architecture:
        raise RuntimeError("Install cloudflared for this platform, or use --local-only")
    bin_dir = state_directory(root) / "bin"
    binary = bin_dir / ("cloudflared.exe" if system == "windows" else "cloudflared")
    record = bin_dir / "cloudflared.json"
    if binary.exists() and record.exists():
        saved = json.loads(record.read_text(encoding="utf-8"))
        if hashlib.sha256(binary.read_bytes()).hexdigest() == saved.get("binary_sha256"):
            return str(binary)
    name = f"cloudflared-{system}-{architecture}" + (".exe" if system == "windows" else ".tgz" if system == "darwin" else "")
    print("Installing a checksum-verified official cloudflared release locally…")
    request = Request("https://api.github.com/repos/cloudflare/cloudflared/releases/latest", headers={"User-Agent": "JANUS-start", "Accept": "application/vnd.github+json"})
    context = verified_tls_context()
    with urlopen(request, timeout=30, context=context) as response:
        release = json.load(response)
    asset = next((item for item in release.get("assets", []) if item.get("name") == name), None)
    if not asset or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset.get("digest") or ""):
        raise RuntimeError("Official release lacks a verifiable asset digest; install cloudflared using its official installer")
    download_url = asset.get("browser_download_url", "")
    if not download_url.startswith("https://github.com/cloudflare/cloudflared/releases/download/"):
        raise RuntimeError("Unexpected cloudflared download origin")
    with urlopen(Request(download_url, headers={"User-Agent": "JANUS-start"}), timeout=60, context=context) as response:
        downloaded = response.read(100 * 1024 * 1024 + 1)
    if len(downloaded) > 100 * 1024 * 1024 or hashlib.sha256(downloaded).hexdigest() != asset["digest"].removeprefix("sha256:"):
        raise RuntimeError("cloudflared checksum verification failed")
    if system == "darwin":
        with tarfile.open(fileobj=io.BytesIO(downloaded), mode="r:gz") as archive:
            members = [item for item in archive.getmembers() if item.isfile() and Path(item.name).name == "cloudflared"]
            if len(members) != 1 or members[0].size > 100 * 1024 * 1024:
                raise RuntimeError("Unexpected cloudflared archive contents")
            downloaded = archive.extractfile(members[0]).read()
    bin_dir.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_suffix(".download")
    temporary.write_bytes(downloaded)
    temporary.chmod(0o700)
    os.replace(temporary, binary)
    atomic_json(record, {"version": release.get("tag_name"), "binary_sha256": hashlib.sha256(downloaded).hexdigest()})
    return str(binary)


def ensure_policy(root: Path, system: str) -> Path:
    sys.path.insert(0, str(root / "agent"))
    from agent.authorization import default_session_policy, load_session_scope
    from agent.validation import load_policies
    path = root / "agent" / ".validation-policy.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Local validation policy must be a JSON object")
        if "session" not in data:
            load_policies(str(path))
            data["session"] = default_session_policy(system)["session"]
            atomic_json(path, data)
    else:
        atomic_json(path, default_session_policy(system))
    load_policies(str(path))
    load_session_scope(str(path))
    return path


def install_dependencies(root: Path, env: dict[str, str], electron: bool, skip: bool) -> None:
    if not shutil.which("uv") or not shutil.which("node"):
        raise RuntimeError("Install uv and Node.js 20.19+ before starting JANUS")
    node_version = subprocess.check_output(["node", "--version"], text=True).strip().lstrip("v")
    parts = tuple(int(value) for value in node_version.split(".")[:2])
    if parts < (20, 19):
        raise RuntimeError("JANUS requires Node.js 20.19 or newer")
    for folder in ("backend", "agent"):
        if skip:
            if not (root / folder / ".venv").exists():
                raise RuntimeError(f"Dependencies missing in {folder}; run startup without --no-install")
        else:
            subprocess.run(["uv", "sync", "--locked", "--directory", str(root / folder)], env=env, check=True)
    lock_digest = hashlib.sha256((root / "package-lock.json").read_bytes()).hexdigest()
    install_record = state_directory(root, env) / "dependencies.json"
    try:
        installed_digest = json.loads(install_record.read_text(encoding="utf-8")).get("package_lock_sha256")
    except (OSError, ValueError):
        installed_digest = None
    if not skip and (installed_digest != lock_digest
                     or not (root / "node_modules" / "vite" / "bin" / "vite.js").exists()
                     or electron and not (root / "node_modules" / "electron" / "dist").exists()):
        install_env = dict(env)
        if not electron:
            install_env["ELECTRON_SKIP_BINARY_DOWNLOAD"] = "1"
        else:
            install_env.pop("ELECTRON_SKIP_BINARY_DOWNLOAD", None)
        npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
        if not npm:
            raise RuntimeError("npm was not found")
        # cmd.exe is needed only to invoke npm.cmd; no user text is interpolated.
        command = ["cmd.exe", "/d", "/c", npm, "ci"] if os.name == "nt" else [npm, "ci"]
        subprocess.run(command, cwd=root, env=install_env, check=True)
        atomic_json(install_record, {"package_lock_sha256": lock_digest})
    if not (root / "node_modules" / "vite" / "bin" / "vite.js").exists():
        raise RuntimeError("JavaScript dependencies missing; run npm ci from the repository root")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Start JANUS backend/database, Studio, phone PWA, tunnels and locally approved laptop controls")
    parser.add_argument("--local-only", action="store_true", help="Skip public phone tunnels")
    parser.add_argument("--no-agent", action="store_true", help="Run web services without laptop controls or permission approval")
    parser.add_argument("--no-browser", action="store_true", help="Keep services in this terminal without opening Studio")
    parser.add_argument("--no-install", action="store_true", help="Use already installed locked dependencies")
    parser.add_argument("--electron", action="store_true", help="Open Electron Studio (the default)")
    parser.add_argument("--studio", choices=("browser", "electron", "none"), default="electron", help="Studio interface (default: electron)")
    parser.add_argument("--only", choices=("agent",), help="Start the locally approved agent against an existing backend")
    parser.add_argument("--startup-approval-stdin", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--imessage", action="store_true", help="Start the configured Photon Spectrum messaging sidecar")
    parser.add_argument("--pwa", action="store_true", help="Phone PWA is included by default (compatibility option)")
    parser.add_argument("--tunnel", action="store_true", help="Public phone tunnels are included by default (compatibility option)")
    parser.add_argument("--screen", action="store_true", help="Screen sharing is included after approval (compatibility option)")
    parser.add_argument("--no-screen", action="store_true", help="Disable laptop screen capture")
    parser.add_argument("--device-id")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--admin-port", type=int, default=8001)
    parser.add_argument("--studio-port", type=int, default=5173)
    parser.add_argument("--pwa-port", type=int, default=5174)
    parser.add_argument("--cloudflared", help="Path to an existing cloudflared executable")
    options = parser.parse_args(argv)
    if options.electron:
        options.studio = "electron"
    options.electron = options.studio == "electron"
    if options.startup_approval_stdin and options.only != "agent":
        parser.error("--startup-approval-stdin is only available for the local desktop agent dialog")
    return options


def main(argv=None) -> int:
    options = parse_args(argv)
    env = load_environment(ROOT / "backend" / ".env", dict(os.environ))
    env["PYTHONUNBUFFERED"] = "1"
    services, tunnels, bootstrap = [], {}, None
    state_dir = state_directory(ROOT, env)
    env["JANUS_STATE_DIR"] = str(state_dir)
    public_urls = state_dir / "public-urls.json"
    owns_public_urls = False
    stopped = threading.Event()
    old_handlers = {}
    try:
        # Install handlers before starting children, so interruption during readiness
        # or tunnel setup also reaches the process-tree cleanup below.
        def interrupt_startup(*_):
            stopped.set()
            raise KeyboardInterrupt

        for signum in (signal.SIGINT, signal.SIGTERM):
            old_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupt_startup)
        if not options.only:
            check_ports([options.backend_port, options.admin_port, options.pwa_port] + ([options.studio_port] if options.studio != "none" else []))
        needs_electron = options.electron and not options.no_browser and not options.only
        install_dependencies(ROOT, env, needs_electron, options.no_install)
        database = ensure_database(ROOT, env) if not options.only else None
        registration = provision_registration(ROOT, env, options.device_id)
        approved = False
        if not options.no_agent:
            if platform.system() not in ("Darwin", "Windows"):
                print("Native controls require macOS or Windows. Starting backend, Studio and PWA for development.")
            else:
                approved = consume_startup_approval() if options.startup_approval_stdin else approve_startup()
                if not approved:
                    print("Laptop permissions declined. Starting web services with laptop controls disabled.")
        policy = ensure_policy(ROOT, platform.system()) if approved else None
        local_backend = f"http://127.0.0.1:{options.backend_port}"
        studio_origin = f"http://127.0.0.1:{options.studio_port}"
        logs_dir = state_dir / "logs"
        env.update(JANUS_TUNNEL_MODE="off", JANUS_PUBLIC_URLS_FILE=str(public_urls), ADMIN_HOST="127.0.0.1", ADMIN_PORT=str(options.admin_port))
        origins = {"backend": None, "pwa": None}
        for name, setting in (("backend", "PUBLIC_BACKEND_URL"), ("pwa", "PWA_PUBLIC_URL")):
            if env.get(setting, "").strip():
                origins[name] = validate_origin(env[setting].strip())
        candidate_urls, next_public_check = {}, {}
        if not options.only:
            atomic_json(public_urls, origins)
            owns_public_urls = True
            atomic_json(state_dir / "controller.json", {"host": local_backend, "device_id": registration.device_id, "token": registration.controller_token})
        redactions = [registration.agent_token, registration.controller_token, *[value for name, value in env.items()
                       if name.endswith(("_TOKEN", "_KEY", "_SECRET", "_PASSWORD")) or name in ("TIGER_DATABASE_URL", "SPECTRUM_PROJECT_ID")]]

        def launch(name, command, cwd=ROOT, extra_env=None, input_data=None):
            service = ManagedProcess(name, command, cwd, {**env, **(extra_env or {})}, logs_dir, redactions=redactions, input_data=input_data, console=True).start()
            services.append(service)
            pid = getattr(getattr(service, "process", None), "pid", None)
            if isinstance(pid, int):
                print(f"[janus-process] {name} {pid}", flush=True)
            return service

        def start_agent():
            command = ["uv", "run", "--no-sync", "--directory", str(ROOT / "agent"), "python", "-m", "agent.main", "connect", "--url", f"ws://127.0.0.1:{options.backend_port}/ws/agents/{registration.device_id}",
                       "--device-id", registration.device_id, "--workers", "4", "--native-tools", "--validation-policy", str(policy), "--session-approval-stdin"]
            if not options.no_screen:
                command.append("--screen")
            if env.get("AGENT_ISOLATION_ADAPTER"):
                command.extend(["--isolation-adapter", env["AGENT_ISOLATION_ADAPTER"]])
            return launch("agent", command, extra_env={"AGENT_TOKEN": registration.agent_token}, input_data="JANUS_SESSION_APPROVED\n")

        if options.only:
            if not approved:
                print("Agent not started: local laptop permission was not approved.", file=sys.stderr)
                return 1
            if not wait_http(local_backend + "/health", 35):
                raise RuntimeError("Start the JANUS backend before starting a standalone agent")
            solo = start_agent()
            for signum in (signal.SIGINT, signal.SIGTERM):
                signal.signal(signum, lambda *_: stopped.set())
            while not stopped.wait(0.5):
                if solo.poll() is not None:
                    return solo.poll() or 0
            return 0

        backend = launch("backend", ["uv", "run", "--no-sync", "--directory", str(ROOT / "backend"), "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(options.backend_port)])
        vite = str(ROOT / "node_modules" / "vite" / "bin" / "vite.js")
        studio = launch("studio", ["node", vite, "--host", "127.0.0.1", "--port", str(options.studio_port), "--strictPort"], ROOT / "frontend", {"VITE_BACKEND_URL": local_backend}) if options.studio != "none" else None
        pwa = launch("pwa", ["node", vite, "--host", "127.0.0.1", "--port", str(options.pwa_port), "--strictPort"], ROOT / "PWA", {"JANUS_PWA_HTTP": "1"})
        for name, url, service in (("backend", local_backend + "/health", backend), ("Studio", studio_origin, studio), ("phone PWA", f"http://127.0.0.1:{options.pwa_port}", pwa)):
            if service is None:
                continue
            if not wait_http(url, 35, service):
                raise RuntimeError(f"{name} did not become ready; inspect {logs_dir}")
        if approved:
            start_agent()
        if options.imessage:
            sidecar_env = imessage_environment(env, local_backend)
            if sidecar_env:
                try:
                    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
                    if not npm:
                        raise RuntimeError("npm was not found")
                    command = ["cmd.exe", "/d", "/c", npm, "run", "build", "--workspace", "@janus/imessage"] if os.name == "nt" else [npm, "run", "build", "--workspace", "@janus/imessage"]
                    subprocess.run(command, cwd=ROOT, env=env, check=True)
                    launch("imessage", ["node", "dist/index.js"], ROOT / "imessage", sidecar_env)
                    print("iMessage sidecar started; the backend enforces the sender allowlist.")
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    print("iMessage build/start failed; local JANUS remains available.", file=sys.stderr)
        print(f"\nLocal JANUS is ready. Studio: {studio_origin}")
        bootstrap = LocalBootstrap(studio_origin, local_backend, registration.controller_token).start() if studio else None
        if options.electron and not options.no_browser:
            launch("electron", ["node", str(ROOT / "frontend" / "electron" / "launch.cjs")], ROOT / "frontend", {"JANUS_STUDIO_URL": bootstrap.browser_url(), "JANUS_BACKEND_URL": local_backend, "AGENT_DEVICE_ID": registration.device_id})
        elif bootstrap and not options.no_browser:
            webbrowser.open(bootstrap.browser_url())

        def check_public(name, address, timeout):
            diagnostics = {}
            ready = wait_http(address + ("/health" if name == "backend" else ""), timeout,
                              tunnels[name].service, diagnostics=diagnostics, cancelled=stopped)
            return ready, diagnostics.get("error", "HTTP readiness check timed out")

        def publish_public(name, address):
            origins[name] = address
            atomic_json(public_urls, origins)
            print(f"Phone {name} address ready: {address}. Create a new pairing code in Studio.")

        if not options.local_only and not all(origins.values()):
            print("Connecting the phone app and backend through Cloudflare…")
            try:
                executable = ensure_cloudflared(ROOT, options.cloudflared)
                from concurrent.futures import ThreadPoolExecutor, as_completed
                pending = {name: CloudflareTunnel(f"tunnel-{name}", executable, port, env, logs_dir) for name, port in (("backend", options.backend_port), ("pwa", options.pwa_port)) if not origins[name]}
                for tunnel in pending.values():
                    tunnel.cancelled = stopped
                tunnels.update(pending)

                def connect_public(name, tunnel):
                    address = tunnel.start()
                    ready, error = check_public(name, address, 35)
                    return address, ready, error

                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = {pool.submit(connect_public, name, tunnel): name for name, tunnel in pending.items()}
                    for future in as_completed(futures):
                        name = futures[future]
                        try:
                            address, ready, error = future.result()
                        except (OSError, ValueError, RuntimeError) as error:
                            print(f"Phone connection unavailable ({name}): {error}", file=sys.stderr)
                            print("Local JANUS remains available; this tunnel will retry independently.", file=sys.stderr)
                            continue
                        candidate_urls[name] = address
                        if ready:
                            publish_public(name, address)
                        else:
                            print(f"Phone {name} public address pending: {error}. Connected tunnel stays running; retrying.", file=sys.stderr)
            except (OSError, ValueError, RuntimeError) as error:
                print(f"Phone connection unavailable: {error}", file=sys.stderr)
                print("Local JANUS remains available. Restart after fixing the tunnel connection, or configure PUBLIC_BACKEND_URL and PWA_PUBLIC_URL.", file=sys.stderr)
        print(f"\nJANUS Studio: {studio_origin}")
        print(f"Local backend: {local_backend}")
        print(f"SQLite database: {database}")
        def phone_status(name):
            if origins[name]:
                return origins[name]
            if options.local_only:
                return "disabled (--local-only)"
            if name in candidate_urls:
                return "pending; retrying public HTTPS checks"
            return "unavailable; check tunnel logs"

        print(f"Phone app: {phone_status('pwa')}")
        print(f"Phone backend: {phone_status('backend')}")
        print(f"Controller credential: {state_dir / 'controller.json'} (private local file; never put it in a QR code)")
        print("Use Project → Laptop controls starter, Save & Push, then Pair Device in Studio.")
        print("Gemini: " + ("configured" if env.get("GEMINI_API_KEY") or env.get("GOOGLE_API_KEY") else "no key configured; built-in tools remain available"))
        print("Tiger Data history: " + ("configured; backend reports connection/migration failures" if env.get("TIGER_DATABASE_URL") else "optional; SQLite panel and request history remains available"))
        if approved:
            print("Generated jobs: " + ("configured local isolation adapter" if env.get("AGENT_ISOLATION_ADAPTER") else "blocked until a local isolation adapter is installed"))
        print("Press Ctrl+C to stop all JANUS services and revoke this session's approval.\n")
        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, lambda *_: stopped.set())
        recovery_attempts = {name: 0 for name in tunnels}
        while not stopped.wait(0.5):
            for service in list(services):
                if service.poll() is not None:
                    if service.name == "imessage":
                        print("iMessage sidecar stopped — texts will not be received. Local JANUS remains available.", file=sys.stderr)
                        mark_sidecar_down(local_backend, env["MESSAGING_SERVICE_TOKEN"])
                        service.stop()
                        services.remove(service)
                        continue
                    if service.name == "agent":
                        print(f"Laptop agent stopped ({service.poll()}); web services remain available. Start it again to approve a new session.", file=sys.stderr)
                        service.stop()
                        services.remove(service)
                        continue
                    if service.name == "electron" and service.poll() == 0:
                        stopped.set()
                        break
                    raise RuntimeError(f"{service.name} exited ({service.poll()}); inspect {logs_dir / (service.name + '.log')}")
            for name, tunnel in list(tunnels.items()):
                if stopped.is_set():
                    break
                if name not in candidate_urls or tunnel.service is None or tunnel.service.poll() is not None:
                    origins[name] = None
                    atomic_json(public_urls, origins)
                    candidate_urls.pop(name, None)
                    if recovery_attempts[name] >= 3:
                        print(f"Cloudflare {name} tunnel repeatedly failed. Local JANUS remains available; inspect its log and restart to reconnect.", file=sys.stderr)
                        tunnel.stop()
                        tunnels.pop(name)
                        continue
                    recovery_attempts[name] += 1
                    print(f"Cloudflare {name} tunnel disconnected; reconnecting ({recovery_attempts[name]}/3)…")
                    tunnel.stop()
                    try:
                        address = tunnel.start()
                        candidate_urls[name] = address
                        ready, error = check_public(name, address, 2)
                        if ready:
                            publish_public(name, address)
                        else:
                            next_public_check[name] = time.monotonic() + 5
                            print(f"Phone {name} public address pending: {error}. Connected tunnel stays running; retrying.", file=sys.stderr)
                    except (OSError, RuntimeError) as error:
                        print(f"Phone {name} reconnect failed: {error}", file=sys.stderr)
                        tunnel.stop()
                elif not origins[name] and time.monotonic() >= next_public_check.get(name, 0):
                    ready, error = check_public(name, candidate_urls[name], 2)
                    next_public_check[name] = time.monotonic() + 5
                    if ready:
                        publish_public(name, candidate_urls[name])
                    else:
                        print(f"Phone {name} public address pending: {error}; will retry.", file=sys.stderr)
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, HTTPError) as error:
        print(f"JANUS startup failed: {error}", file=sys.stderr)
        return 1
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)

        def cleanup(action):
            try:
                action()
            except (OSError, RuntimeError, subprocess.SubprocessError) as error:
                print(f"JANUS cleanup: {error}", file=sys.stderr)

        if owns_public_urls:
            cleanup(lambda: atomic_json(public_urls, {"backend": None, "pwa": None}))
        if bootstrap:
            cleanup(bootstrap.stop)
        if options.imessage and env.get("MESSAGING_SERVICE_TOKEN"):
            cleanup(lambda: mark_sidecar_down(f"http://127.0.0.1:{options.backend_port}", env["MESSAGING_SERVICE_TOKEN"]))
        for tunnel in reversed(list(tunnels.values())):
            cleanup(tunnel.stop)
        for service in reversed(services):
            cleanup(service.stop)


if __name__ == "__main__":
    raise SystemExit(main())
