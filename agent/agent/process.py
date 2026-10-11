"""Bounded capture for trusted toolchain commands; this is NOT an OS sandbox."""
import asyncio
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0
    timed_out: bool = False
    output_limited: bool = False
    unavailable: bool = False
    cancelled: bool = False


async def finish_thread(function, *args):
    """Finish owned file IO before propagating cancellation and deleting its files."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Threads cannot be forcibly cancelled. Windows cannot delete an open
        # executable, so its hash reader must finish before artifact cleanup.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise


def run_bounded(command, *, cwd, timeout=30, stdout_limit=65536, stderr_limit=65536, cancel_event=None):
    """Bound memory and wall time; callers still need an OS boundary for untrusted code."""
    start = time.monotonic()
    if cancel_event is not None and cancel_event.is_set():
        return ProcessResult(None, cancelled=True)
    kwargs = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    try:
        process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except OSError:
        return ProcessResult(None, elapsed_ms=int((time.monotonic() - start) * 1000), unavailable=True)
    overflow = threading.Event()
    buffers = [bytearray(), bytearray()]

    def drain(pipe, buffer, limit):
        try:
            while data := pipe.read(4096):
                remaining = limit - len(buffer)
                buffer.extend(data[:max(0, remaining)])
                if len(data) > remaining:
                    overflow.set()
        finally:
            pipe.close()

    readers = [threading.Thread(target=drain, args=(pipe, buffer, limit), daemon=True) for pipe, buffer, limit in zip((process.stdout, process.stderr), buffers, (stdout_limit, stderr_limit))]
    for reader in readers:
        reader.start()
    timed_out = False
    cancelled = False
    deadline = start + timeout
    try:
        while process.poll() is None and not overflow.is_set():
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.01)
    finally:
        # Also clean up when the local user interrupts a trusted build. Kill
        # the group if a departed leader left descendants holding pipe handles.
        if process.poll() is None or any(reader.is_alive() for reader in readers):
            if os.name == "nt":
                taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
                try:
                    subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        process.wait()
        for reader in readers:
            reader.join(timeout=1)
    return ProcessResult(process.returncode, buffers[0].decode("utf-8", "replace"), buffers[1].decode("utf-8", "replace"), int((time.monotonic() - start) * 1000), timed_out, overflow.is_set(), cancelled=cancelled)
