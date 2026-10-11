"""Owned, parallel processes for the repository's fixed native tools."""
import asyncio

from .bridge import MAX_LINE, NativeProcess, command_line


class LiveNative:
    def __init__(self, binaries, options):
        self.binaries, self.options = binaries, options
        self.tools, self.tasks, self.queues = {}, [], {}

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
            tool = self.tools[name] = NativeProcess(name, process, outbox)
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
            request_id, line = await self.queues[name].get()
            result = await self.tools[name].command(line)
            await outbox.result({**result, "request_id": request_id})

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
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await asyncio.gather(*(tool.close() for tool in self.tools.values()))
