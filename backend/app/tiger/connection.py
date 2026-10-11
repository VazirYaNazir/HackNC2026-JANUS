"""Connection settings for the optional Tiger Data (TimescaleDB on Tiger Cloud) store.

Everything in app.tiger is a no-op unless TIGER_DATABASE_URL is set. SQLite remains the application database.
"""
import asyncio
import ipaddress
import os
import socket

try:  # optional dependency: the backend must still start (with Tiger Data off) if psycopg is not installed
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
except ImportError:  # pragma: no cover - exercised by a test that hides the module
    psycopg = None

CONNECT_TIMEOUT_SECONDS = 5


def tiger_url() -> str | None:
    url = os.getenv("TIGER_DATABASE_URL", "").strip()
    return url or None


def _is_address(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def resolve_ipv4(host: str, port: int) -> str | None:
    """First IPv4 address for `host`, or None. Used because IPv6 is broken on some networks."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return None
    return infos[0][4][0] if infos else None


def resolve_ipv4_blocking(host: str, port: int) -> str | None:
    try:
        infos = socket.getaddrinfo(host, port, family=socket.AF_INET, type=socket.SOCK_STREAM)
    except OSError:
        return None
    return infos[0][4][0] if infos else None


def require_psycopg() -> None:
    if psycopg is None:
        raise RuntimeError("Tiger Data needs the 'psycopg' package; run `npm run setup` (or `uv sync --locked` in backend/).")


def conninfo_for(url: str, ipv4: str | None = None) -> str:
    """libpq conninfo with a bounded connect timeout and, when known, the IPv4 `hostaddr`.

    `host` stays the DNS name, so TLS verification and SNI still use the real hostname while the TCP connection goes to
    the IPv4 address (libpq's hostaddr)."""
    info = conninfo_to_dict(url)
    if ipv4:
        info["hostaddr"] = ipv4
    info.setdefault("connect_timeout", str(CONNECT_TIMEOUT_SECONDS))
    return make_conninfo(**info)


def connect(url: str, *, autocommit: bool = False) -> "psycopg.Connection":
    """Blocking connection preferring IPv4. Call from a worker thread, never from the event loop."""
    require_psycopg()
    info = conninfo_to_dict(url)
    host = info.get("host") or ""
    ipv4 = None
    if host and not host.startswith("/") and not _is_address(host):
        ipv4 = resolve_ipv4_blocking(host, int(info.get("port") or 5432))
    return psycopg.connect(conninfo_for(url, ipv4), autocommit=autocommit)


# What a failed connection or query raises; empty when psycopg is missing (connect() then raises RuntimeError itself).
DB_ERRORS: tuple = (psycopg.Error,) if psycopg is not None else ()
