"""Optional standalone quick tunnel; the full JANUS launcher manages its own."""
import asyncio
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)
_tunnel_url: Optional[str] = None
_tunnel_process: Optional[asyncio.subprocess.Process] = None
_drain_task: Optional[asyncio.Task] = None
_start_task: Optional[asyncio.Task] = None

TUNNEL_REGEX = re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com")


def get_tunnel_url() -> Optional[str]:
    """Return only a connected, live tunnel; assigned hostnames are not ready."""
    global _tunnel_url
    if _tunnel_process is None or _tunnel_process.returncode is not None:
        _tunnel_url = None
    return _tunnel_url


async def _terminate(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        except OSError as exc:
            logger.warning("Could not terminate cloudflared: %s", type(exc).__name__)
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()


async def _launch(port: int, timeout: float) -> str:
    global _tunnel_url, _tunnel_process, _drain_task
    process = None
    reader = None
    ready = asyncio.get_running_loop().create_future()
    try:
        try:
            process = await asyncio.create_subprocess_exec(
                "cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError:
            raise RuntimeError("cloudflared executable not found in PATH.") from None
        _tunnel_process = process

        async def monitor() -> None:
            global _tunnel_url, _tunnel_process
            candidate = None
            connected = False
            try:
                if process.stdout is None:
                    raise RuntimeError("cloudflared output pipe unavailable")
                while line_bytes := await process.stdout.readline():
                    line = line_bytes.decode("utf-8", errors="replace")
                    match = TUNNEL_REGEX.search(line)
                    if match:
                        candidate = match.group(0)
                    if "Registered tunnel connection" in line:
                        connected = True
                    if candidate and connected and not ready.done():
                        _tunnel_url = candidate
                        ready.set_result(candidate)
                # EOF can precede wait() completing; stop advertising immediately.
                if _tunnel_process is process:
                    _tunnel_url = None
                await process.wait()
                if not ready.done():
                    ready.set_exception(RuntimeError("cloudflared exited before its tunnel connected"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not ready.done():
                    ready.set_exception(exc)
                else:
                    logger.warning("Cloudflare tunnel output failed: %s", type(exc).__name__)
            finally:
                if _tunnel_process is process:
                    _tunnel_url = None

        reader = asyncio.create_task(monitor(), name="janus-tunnel-output")
        _drain_task = reader
        url = await asyncio.wait_for(ready, timeout=timeout)
        if process.returncode is not None or _tunnel_url != url:
            raise RuntimeError("cloudflared exited after assigning its tunnel URL")
        logger.info("Cloudflare tunnel connected at %s", url)
        return url
    except BaseException as exc:
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        if process is not None:
            await _terminate(process)
        if _tunnel_process is process:
            _tunnel_process = None
            _tunnel_url = None
            _drain_task = None
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise RuntimeError(f"Failed to start Cloudflare Tunnel: {exc}") from exc


async def start_tunnel(port: int = 8000, *, timeout: float = 45) -> str:
    """Share concurrent starts and wait for Cloudflare connection readiness."""
    global _start_task
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("Invalid tunnel target port")
    live = get_tunnel_url()
    if live:
        return live
    if _start_task is None or _start_task.done():
        # A previous disconnected process may still need reaping.
        await stop_tunnel()
        _start_task = asyncio.create_task(_launch(port, timeout), name="janus-tunnel-start")
    task = _start_task
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await stop_tunnel()
        raise
    finally:
        if task.done() and _start_task is task:
            _start_task = None


async def stop_tunnel() -> None:
    """Cancel startup/output readers, terminate the child, and reap it."""
    global _tunnel_url, _tunnel_process, _drain_task, _start_task
    starter, reader, process = _start_task, _drain_task, _tunnel_process
    _tunnel_url = None
    _start_task = None
    if starter and not starter.done():
        starter.cancel()
        await asyncio.gather(starter, return_exceptions=True)
    if reader and not reader.done():
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
    # A cancelled launch has already reaped its process.
    if process is not None and _tunnel_process is process:
        await _terminate(process)
    _tunnel_process = None
    _drain_task = None


def __getattr__(name: str):
    if name == "tunnel_url":
        return get_tunnel_url()
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
