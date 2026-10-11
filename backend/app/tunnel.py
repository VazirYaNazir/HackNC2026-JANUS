# app/tunnel.py
import asyncio
import logging
import re
from typing import Optional

# uvicorn's logger, like the rest of the backend: a module-named logger has no handler and its INFO lines never show.
logger = logging.getLogger("uvicorn.error")

_tunnel_url: Optional[str] = None
_tunnel_process: Optional[asyncio.subprocess.Process] = None
_drain_task: Optional[asyncio.Task] = None

# "api.trycloudflare.com" is the Cloudflare API host that cloudflared mentions in its own log lines, not a tunnel.
TUNNEL_REGEX = re.compile(r"https://(?!api\.)[a-zA-Z0-9-]+\.trycloudflare\.com")


def get_tunnel_url() -> Optional[str]:
    """Returns the active Cloudflare Tunnel public URL, or None if not running."""
    return _tunnel_url


async def start_tunnel(port: int = 8000) -> str:
    """Executes cloudflared tunnel --url http://localhost:<port> as a non-blocking subprocess,
    captures the assigned https://*.trycloudflare.com URL, and stores it in memory.
    """
    global _tunnel_url, _tunnel_process, _drain_task

    if _tunnel_process is not None and _tunnel_url:
        return _tunnel_url

    cmd = ["cloudflared", "tunnel", "--url", f"http://localhost:{port}"]
    try:
        _tunnel_process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except FileNotFoundError:
        logger.error("cloudflared executable not found in PATH.")
        raise RuntimeError("cloudflared executable not found in PATH.")

    async def _extract_url() -> str:
        assert _tunnel_process is not None and _tunnel_process.stdout is not None
        while True:
            line_bytes = await _tunnel_process.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace")
            match = TUNNEL_REGEX.search(line)
            if match:
                return match.group(0)
        return ""

    try:
        captured_url = await asyncio.wait_for(_extract_url(), timeout=30.0)
        if not captured_url:
            returncode = _tunnel_process.returncode
            raise RuntimeError(
                f"cloudflared exited unexpectedly (code {returncode}) without producing a tunnel URL."
            )
        _tunnel_url = captured_url
        logger.info(f"Cloudflare Tunnel active at: {_tunnel_url}")
    except Exception as exc:
        await stop_tunnel()
        raise RuntimeError(f"Failed to start Cloudflare Tunnel: {exc}") from exc

    async def _drain_output():
        try:
            assert _tunnel_process is not None and _tunnel_process.stdout is not None
            while True:
                line = await _tunnel_process.stdout.readline()
                if not line:
                    break
        except Exception:
            pass

    _drain_task = asyncio.create_task(_drain_output())
    return _tunnel_url


async def stop_tunnel() -> None:
    """Terminates the cloudflared subprocess and clears in-memory state."""
    global _tunnel_url, _tunnel_process, _drain_task

    if _drain_task and not _drain_task.done():
        _drain_task.cancel()
        try:
            await _drain_task
        except asyncio.CancelledError:
            pass
    _drain_task = None

    if _tunnel_process is not None:
        try:
            _tunnel_process.terminate()
            wait_res = _tunnel_process.wait()
            if asyncio.iscoroutine(wait_res):
                await asyncio.wait_for(wait_res, timeout=5.0)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                kill_res = _tunnel_process.kill()
                if asyncio.iscoroutine(kill_res):
                    await kill_res
                wait_res = _tunnel_process.wait()
                if asyncio.iscoroutine(wait_res):
                    await wait_res
            except Exception:
                pass
        except Exception as exc:
            logger.warning(f"Error stopping cloudflared tunnel: {exc}")
        finally:
            _tunnel_process = None
            _tunnel_url = None


def __getattr__(name: str):
    if name == "tunnel_url":
        return _tunnel_url
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
