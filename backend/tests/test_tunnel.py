"""Quick-tunnel readiness, cancellation and child lifecycle regression tests."""
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app import tunnel

URL = 'https://fixture-name.trycloudflare.com'


class FakeProcess:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.stdout = self
        self.returncode = None
        self.exited = asyncio.Event()
        self.terminate = MagicMock(side_effect=self.exit)
        self.kill = MagicMock(side_effect=self.exit)
        self.wait = AsyncMock(side_effect=self.wait_for_exit)

    async def readline(self):
        result = await self.queue.get()
        if isinstance(result, BaseException):
            raise result
        return result

    def line(self, value):
        self.queue.put_nowait(value.encode())

    def exit(self, code=0):
        self.returncode = code
        self.queue.put_nowait(b'')
        self.exited.set()

    async def wait_for_exit(self):
        await self.exited.wait()
        return self.returncode


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = patch.multiple(tunnel, _tunnel_process=None, _tunnel_url=None,
                                    _drain_task=None, _start_task=None)
        self.state.start()

    async def asyncTearDown(self):
        await tunnel.stop_tunnel()
        self.state.stop()

    async def launched(self, process):
        for _ in range(20):
            if tunnel._tunnel_process is process:
                return
            await asyncio.sleep(0)
        self.fail('subprocess did not launch')

    async def test_concurrent_start_waits_for_connection_and_uses_requested_port(self):
        process = FakeProcess()
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process) as spawn:
            first = asyncio.create_task(tunnel.start_tunnel(9010))
            second = asyncio.create_task(tunnel.start_tunnel(9010))
            await self.launched(process)
            process.line('Assigned URL ' + URL)
            await asyncio.sleep(0)
            self.assertIsNone(tunnel.get_tunnel_url())
            self.assertFalse(first.done())
            process.line('Registered tunnel connection connIndex=0')
            self.assertEqual(await asyncio.gather(first, second), [URL, URL])
            self.assertEqual(await tunnel.start_tunnel(9010), URL)
            spawn.assert_awaited_once()
            self.assertIn('http://127.0.0.1:9010', spawn.call_args.args)
            self.assertEqual(tunnel.tunnel_url, URL)
            await tunnel.stop_tunnel()
            process.terminate.assert_called_once()
            self.assertTrue(process.exited.is_set())
            self.assertIsNone(tunnel.get_tunnel_url())
        with self.assertRaises(AttributeError):
            getattr(tunnel, 'missing')

    async def test_unexpected_exit_clears_url_and_a_fresh_start_recovers(self):
        process = FakeProcess()
        process.line(URL)
        process.line('Registered tunnel connection')
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process):
            self.assertEqual(await tunnel.start_tunnel(), URL)
        process.exit(1)
        self.assertIsNone(tunnel.get_tunnel_url())
        await tunnel._drain_task
        replacement = FakeProcess()
        replacement.line('Registered tunnel connection')
        replacement.line('https://replacement.trycloudflare.com')
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=replacement):
            self.assertEqual(await tunnel.start_tunnel(), 'https://replacement.trycloudflare.com')

    async def test_api_hostname_is_ignored_and_connected_url_reaches_console_log(self):
        process = FakeProcess()
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process), self.assertLogs('uvicorn.error', level='INFO') as logs:
            start = asyncio.create_task(tunnel.start_tunnel())
            await self.launched(process)
            process.line('Post "https://api.trycloudflare.com/tunnel": dial failed')
            process.line('Registered tunnel connection')
            await asyncio.sleep(0)
            self.assertIsNone(tunnel.get_tunnel_url())
            self.assertFalse(start.done())
            process.line(URL)
            self.assertEqual(await start, URL)
        self.assertIn('Cloudflare tunnel connected at ' + URL, ''.join(logs.output))

    async def test_missing_binary_early_exit_and_pipe_failure_leave_no_stale_state(self):
        with patch('app.tunnel.asyncio.create_subprocess_exec', side_effect=FileNotFoundError), self.assertRaisesRegex(RuntimeError, 'not found'):
            await tunnel.start_tunnel()
        for failure in ('exit', 'pipe'):
            process = FakeProcess()
            if failure == 'exit':
                process.exit(2)
            else:
                process.queue.put_nowait(OSError('fixture pipe failure'))
            with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process), self.assertRaisesRegex(RuntimeError, 'Failed to start'):
                await tunnel.start_tunnel()
            self.assertIsNone(tunnel._tunnel_process)
            self.assertIsNone(tunnel.get_tunnel_url())
            self.assertTrue(process.exited.is_set())

    async def test_timeout_and_cancel_reap_starting_child(self):
        process = FakeProcess()
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process), self.assertRaises(RuntimeError):
            await tunnel.start_tunnel(timeout=0.01)
        process.terminate.assert_called_once()
        self.assertIsNone(tunnel._tunnel_process)
        process = FakeProcess()
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process):
            start = asyncio.create_task(tunnel.start_tunnel())
            await self.launched(process)
            await tunnel.stop_tunnel()
            with self.assertRaises(asyncio.CancelledError):
                await start
            process.terminate.assert_called_once()
            self.assertIsNone(tunnel._drain_task)

    async def test_stubborn_child_is_killed_and_reaped_after_terminate_timeout(self):
        process = FakeProcess()
        process.terminate = MagicMock()
        process.wait = AsyncMock(side_effect=[TimeoutError(), 0])
        tunnel._tunnel_process = process
        await tunnel.stop_tunnel()
        process.kill.assert_called_once()
        self.assertEqual(process.wait.await_count, 2)

    async def test_invalid_ports_never_spawn(self):
        with patch('app.tunnel.asyncio.create_subprocess_exec') as spawn:
            for value in (True, 0, 65536, '8000'):
                with self.subTest(port=value), self.assertRaises(ValueError):
                    await tunnel.start_tunnel(value)
            spawn.assert_not_called()
