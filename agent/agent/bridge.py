"""Bounded live telemetry forwarding and a fixed native command vocabulary."""
import asyncio
import json
import math
import sys
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK, InvalidHandshake

from .jobs import JOB_MESSAGES

MAX_LINE = 6 * 1024 * 1024


def scalar(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a numeric argument")
    try:
        valid = math.isfinite(value) and low <= value <= high
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"Argument must be between {low} and {high}")
    return value


def validate_options(options):
    url = urlsplit(options.url or "")
    if url.scheme not in ("ws", "wss") or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("Provide --url wss://host/ws/agent or set AGENT_BACKEND_URL")
    if url.scheme == "ws" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Remote backends require wss://; ws:// is allowed only on loopback")
    if not url.path:
        raise ValueError("Backend URL must include the WebSocket path, typically /ws/agent")
    scalar(options.screen_interval, 200, 60000)
    scalar(options.min_cpu, 0, 100000)
    scalar(options.min_ram_mb, 0, 1000000)


def command_line(message):
    """Translate allowed JSON actions; never accept source, binaries or shell text."""
    args = message.get("args", {})
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    target, action = message.get("target"), message.get("action")
    if target == "volume" and action == "set":
        return "volume", f"set {scalar(args.get('value'), 0, 100):.6f}\n"
    if target == "mouse":
        if action == "move":
            x, y = scalar(args.get("x"), -1, 1), scalar(args.get("y"), -1, 1)
            return "mouse", f"move {x:.6f} {y:.6f}\n"
        if action == "button" and args.get("button") in ("left", "right", "middle") and args.get("state") in ("up", "down"):
            return "mouse", f"button {args['button']} {args['state']}\n"
        if action == "scroll":
            amount = scalar(args.get("amount"), -100, 100)
            if int(amount) != amount:
                raise ValueError("Scroll amount must be an integer")
            return "mouse", f"scroll {int(amount)}\n"
    raise ValueError("Unsupported target/action")


class Outbox:
    """Keep the latest sample per stream when a backend is slower than capture."""
    def __init__(self):
        self.samples = {}
        self.results = asyncio.Queue(maxsize=64)
        self.ready = asyncio.Event()

    def sample(self, event):
        self.samples[event["stream"]] = event
        self.ready.set()

    async def result(self, event):
        await self.results.put(event)
        self.ready.set()

    async def get(self):
        while True:
            if not self.results.empty():
                return self.results.get_nowait()
            if self.samples:
                key = next(iter(self.samples))
                return self.samples.pop(key)
            self.ready.clear()
            await self.ready.wait()


class NativeProcess:
    def __init__(self, name, process, outbox):
        self.name, self.process, self.outbox = name, process, outbox
        self.pending = None
        self.command_lock = asyncio.Lock()

    async def read(self):
        while True:
            try:
                line = await self.process.stdout.readline()
            except ValueError as error:
                raise RuntimeError(f"{self.name}: native output exceeds framing limit") from error
            if not line:
                break
            try:
                event = json.loads(line)
                if not isinstance(event, dict) or event.get("stream") != self.name or event.get("type") not in ("telemetry", "command_result", "error"):
                    raise ValueError("Invalid native event")
            except (ValueError, UnicodeError) as error:
                raise RuntimeError(f"{self.name}: invalid native JSON output") from error
            if event["type"] == "command_result":
                if self.pending and not self.pending.done():
                    self.pending.set_result(event)
            else:
                self.outbox.sample(event)
        raise RuntimeError(f"{self.name}: native process exited")

    async def command(self, line):
        async with self.command_lock:
            self.pending = asyncio.get_running_loop().create_future()
            try:
                self.process.stdin.write(line.encode("ascii"))
                await self.process.stdin.drain()
                return await asyncio.wait_for(self.pending, 2)
            except TimeoutError as error:
                # A late acknowledgement could otherwise belong to the next command.
                raise RuntimeError(f"{self.name}: command acknowledgement timed out") from error
            finally:
                self.pending = None

    async def close(self):
        if self.process.returncode is not None:
            return
        if self.name in ("mouse", "volume"):
            try:
                self.process.stdin.write(b"stop\n")
                await self.process.stdin.drain()
                await asyncio.wait_for(self.process.wait(), 0.5)
                return
            except (OSError, TimeoutError):
                pass
        try:
            self.process.terminate()
            await asyncio.wait_for(self.process.wait(), 1)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        except ProcessLookupError:
            pass


async def receive_commands(websocket, tools, outbox, validation=None):
    queues = {name: asyncio.Queue(maxsize=32) for name in tools if name in ("volume", "mouse")}

    async def command_worker(name):
        while True:
            request_id, line = await queues[name].get()
            result = await tools[name].command(line)
            await outbox.result({**result, "request_id": request_id})

    tasks = [asyncio.create_task(command_worker(name)) for name in queues]
    tasks.append(asyncio.create_task(read_commands(websocket, queues, outbox, validation)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def read_commands(websocket, queues, outbox, validation=None):
    """Keep receiving jobs/revisions while each device drains its ordered queue."""
    async for payload in websocket:
        message = None
        try:
            message = json.loads(payload)
            if not isinstance(message, dict):
                raise ValueError("Message must be an object")
            if message.get("type") in JOB_MESSAGES:
                if validation is None:
                    await outbox.result({"type": "validation_result", "job_id": None, "status": "REJECTED", "reason": "VALIDATION_UNAVAILABLE"})
                else:
                    await validation.handle(message)
                continue
            if message.get("type") == "job":
                job_id = message.get("job_id")
                if not isinstance(job_id, str) or len(job_id) > 128:
                    raise ValueError("job_id must be a string of at most 128 characters")
                args = message.get("args", {})
                if not isinstance(args, dict):
                    raise ValueError("args must be an object")
                if message.get("action") == "ping":
                    result = {"pong": True}
                elif message.get("action") == "add":
                    a, b = scalar(args.get("a"), -1e100, 1e100), scalar(args.get("b"), -1e100, 1e100)
                    result = {"sum": a + b}
                else:
                    raise ValueError("Unsupported job action")
                await outbox.result({"type": "job_result", "job_id": job_id, "ok": True, "result": result})
                continue
            if message.get("type") != "command":
                raise ValueError("Expected command or job")
            request_id = message.get("request_id")
            if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
                raise ValueError("request_id must be a nonempty string of at most 128 characters")
            name, line = command_line(message)
            try:
                queues[name].put_nowait((request_id, line))
            except asyncio.QueueFull as error:
                raise ValueError("Too many pending commands for target") from error
        except (ValueError, TypeError, KeyError) as error:
            is_job = isinstance(message, dict) and message.get("type") == "job"
            await outbox.result({
                "type": "job_result" if is_job else "command_result",
                "job_id" if is_job else "request_id": message.get("job_id" if is_job else "request_id") if isinstance(message, dict) else None,
                "ok": False, "error": str(error),
            })


async def send_events(websocket, outbox):
    while True:
        await websocket.send(json.dumps(await outbox.get(), separators=(",", ":"), allow_nan=False))


async def connected_session(websocket, options, binaries, validation_service=None):
    outbox = Outbox()
    validation = validation_service.connect(outbox) if validation_service else None
    tools, tasks = {}, []
    args = {
        "volume": [], "mouse": [],
        "metrics": ["--temperature-file", options.temperature_file] if options.temperature_file else [],
        "processes": ["--min-cpu", str(options.min_cpu), "--min-ram-mb", str(options.min_ram_mb)],
    }
    if options.screen:
        args["screen"] = ["--interval", str(options.screen_interval), "--request-permission"]

    async def start(name, argv):
        process = await asyncio.create_subprocess_exec(str(binaries[name]), *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=MAX_LINE)
        tools[name] = NativeProcess(name, process, outbox)
        tasks.append(asyncio.create_task(tools[name].read()))

    startups = [asyncio.create_task(start(name, argv)) for name, argv in args.items()]
    try:
        # Let every spawn finish before cleanup even if one fails or the session
        # is cancelled, so no concurrently started process escapes shutdown.
        await asyncio.shield(asyncio.gather(*startups))
        await outbox.result({"type": "agent_hello", "protocol": 1, "streams": list(args), "commands": ["volume.set", "mouse.move", "mouse.button", "mouse.scroll"],
                             "validation": {"protocol": 1, "max_source_bytes": 131072, "max_attempts": 3,
                                            "build_boundary_available": bool(validation_service and validation_service.boundary.can_build()),
                                            "execution_boundary_available": bool(validation_service and validation_service.boundary.can_run())}})
        tasks.extend((asyncio.create_task(send_events(websocket, outbox)), asyncio.create_task(receive_commands(websocket, tools, outbox, validation))))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                task.result()
            except ConnectionClosedOK:
                # A backend can close normally while a final sample is sending.
                pass
    finally:
        await asyncio.gather(*startups, return_exceptions=True)
        if validation:
            await validation.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*(tool.close() for tool in tools.values()))


async def run_bridge(options, binaries, token, validation_service=None):
    retry = 1
    while True:
        try:
            # Permit JSON escaping of a full 128 KiB source; validation still
            # applies the smaller decoded UTF-8 source limit before compiling.
            async with connect(options.url, additional_headers={"Authorization": f"Bearer {token}"}, max_size=1024 * 1024, close_timeout=3) as websocket:
                retry = 1
                print("Backend connected; native telemetry active", file=sys.stderr)
                await connected_session(websocket, options, binaries, validation_service)
        except (ConnectionClosed, InvalidHandshake, OSError, RuntimeError) as error:
            print(f"Connection/tool interrupted ({type(error).__name__}); reconnecting in {retry}s", file=sys.stderr)
        await asyncio.sleep(retry)
        retry = min(retry * 2, 30)
