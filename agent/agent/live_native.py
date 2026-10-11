"""Owned, parallel processes for the repository's fixed native tools."""
import asyncio
from copy import deepcopy

from .bridge import MAX_LINE, NativeProcess, command_line


class LiveNative:
    def __init__(self, binaries, options):
        self.binaries, self.options = binaries, options
        self.tools, self.tasks, self.queues = {}, [], {}
        self.latest, self.closed = {}, False

    @property
    def streams(self):
        return [name for name in self.binaries if name != "screen" or self.options.screen]

    async def start(self, outbox):
        args = {"volume": [], "mouse": [],
                "metrics": ["--temperature-file", self.options.temperature_file] if self.options.temperature_file else [],
                "processes": ["--min-cpu", str(self.options.min_cpu), "--min-ram-mb", str(self.options.min_ram_mb)],
                "screen": ["--interval", str(self.options.screen_interval), "--max-width", "960", "--request-permission"]}

        async def launch(name):
            process = await asyncio.create_subprocess_exec(str(self.binaries[name]), *args[name],
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, limit=MAX_LINE)
            tool = self.tools[name] = NativeProcess(name, process, SampleOutbox(self, outbox))
            self.tasks.append(asyncio.create_task(tool.read()))
            if name in ("volume", "mouse"):
                self.queues[name] = asyncio.Queue(32)
                self.tasks.append(asyncio.create_task(self.worker(name, outbox)))

        startups = [asyncio.create_task(launch(name)) for name in self.streams]
        try:
            await asyncio.shield(asyncio.gather(*startups))
        finally:
            await asyncio.gather(*startups, return_exceptions=True)

    async def worker(self, name, outbox):
        while True:
            request_id, line, *waiting = await self.queues[name].get()
            future = waiting[0] if waiting else None
            try:
                if future is not None and future.done():
                    continue
                result = await self.tools[name].command(line)
                self.record(result)
                if future is not None:
                    if not future.done():
                        future.set_result(result)
                else:
                    await outbox.result({**result, "request_id": request_id})
            except BaseException as error:
                if future is not None and not future.done():
                    if isinstance(error, asyncio.CancelledError):
                        future.cancel()
                    else:
                        future.set_exception(error)
                raise
            finally:
                self.queues[name].task_done()

    def record(self, event):
        name = event["stream"]
        if event["type"] in ("telemetry", "command_result") and isinstance(event.get("data"), dict):
            self.latest[name] = deepcopy(event["data"])
        elif event["type"] == "error":
            self.latest.pop(name, None)

    def snapshot(self, name):
        return deepcopy(self.latest.get(name))

    def control_available(self, name):
        return not self.closed and name in self.queues and name in self.tools

    async def execute(self, payload):
        """Use the same per-tool ordering as frontend controls, with an owned ack."""
        if set(payload) != {"target", "action", "args"}:
            raise ValueError("Invalid native command fields")
        name, line = command_line(payload)
        if not self.control_available(name):
            raise ValueError("Native control is not enabled locally")
        future = asyncio.get_running_loop().create_future()
        try:
            self.queues[name].put_nowait((None, line, future))
        except asyncio.QueueFull:
            raise ValueError("Native control queue is full") from None
        # Awaiting the owned future propagates caller cancellation to it. The
        # queue worker skips that cancelled request before writing to the tool.
        return await future

    def command(self, payload):
        if set(payload) != {"request_id", "target", "action", "args"}:
            raise ValueError("Invalid native command fields")
        request_id = payload["request_id"]
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("Invalid native request identifier")
        name, line = command_line(payload)
        if name not in self.queues:
            raise ValueError("Native control is not enabled locally")
        try:
            self.queues[name].put_nowait((request_id, line))
        except asyncio.QueueFull:
            raise ValueError("Native control queue is full") from None

    async def close(self):
        self.closed = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for queue in self.queues.values():
            while not queue.empty():
                _, _, *waiting = queue.get_nowait()
                if waiting and not waiting[0].done():
                    waiting[0].cancel()
                queue.task_done()
        await asyncio.gather(*(tool.close() for tool in self.tools.values()))


class SampleOutbox:
    """Tee owned telemetry into local snapshots without consuming live events."""
    def __init__(self, native, outbox):
        self.native, self.outbox = native, outbox

    def sample(self, event):
        self.native.record(event)
        self.outbox.sample(event)
