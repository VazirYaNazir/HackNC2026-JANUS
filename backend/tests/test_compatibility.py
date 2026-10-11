"""Unit coverage for retained admin/legacy APIs and tunnel lifecycle."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient
from app import db, tunnel
from app.main import admin_app, admin_health, health
from app.routes import agent, scripts


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(); self.path = patch.object(db, 'DB_PATH', Path(self.folder.name) / 'app.db'); self.path.start(); db.init_db()
        self.secret = patch.object(scripts.settings, 'ADMIN_SECRET', 'admin-test-fixture'); self.secret.start(); self.client = TestClient(admin_app)
    def tearDown(self):
        self.client.close(); self.secret.stop(); self.path.stop(); self.folder.cleanup()
    def test_admin_secret_required_and_script_create_list_delete_roundtrip(self):
        self.assertEqual(self.client.get('/api/v1/scripts/').status_code, 403)
        headers = {'X-Admin-Secret': 'admin-test-fixture'}
        response = self.client.post('/api/v1/scripts/', headers=headers, json={'name': 'Test', 'code': 'trusted local text', 'widget': {'widget_type': 'button', 'label': 'Run', 'default_val': '42'}})
        self.assertEqual(response.status_code, 200); script_id = response.json()['script_id']
        self.assertEqual(len(self.client.get('/api/v1/scripts/', headers=headers).json()), 1)
        self.assertEqual(self.client.delete('/api/v1/scripts/' + script_id, headers=headers).status_code, 200)
        self.assertEqual(self.client.delete('/api/v1/scripts/' + script_id, headers=headers).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/scripts/devices', headers=headers).json(), {'devices': []})
        self.assertEqual(self.client.delete('/api/v1/scripts/devices/missing', headers=headers).status_code, 404)
    def test_pairing_tunnel_override_fallback_and_revoke(self):
        with patch('app.routes.scripts.get_tunnel_url', return_value='https://fixture.trycloudflare.com'):
            self.assertTrue(scripts.request_pairing_code()['qr_payload'].startswith('https://fixture.trycloudflare.com?'))
            self.assertTrue(scripts.request_pairing_code('https://public.example/')['qr_payload'].startswith('https://public.example?'))
        with patch('app.routes.scripts.get_tunnel_url', return_value=None): self.assertTrue(scripts.request_pairing_code()['qr_payload'].startswith('http://localhost:8000?'))
        with patch('app.routes.scripts.revoke_device_by_id', return_value=True): self.assertEqual(scripts.revoke_device('phone')['status'], 'revoked')


class LegacyAgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tokens = patch.multiple(agent, AGENT_TOKEN='agent-fixture', CONTROL_API_KEY='controller-fixture', connected_agent=None, pending_jobs={}); self.tokens.start()
    async def asyncTearDown(self): self.tokens.stop()
    async def test_connection_auth_rejects_wrong_tokens_and_second_connections(self):
        socket = SimpleNamespace(headers={}, close=AsyncMock(), accept=AsyncMock())
        await agent.agent_websocket(socket); socket.close.assert_awaited_once_with(code=1008); socket.accept.assert_not_awaited()
        socket.headers = {'authorization': 'Bearer agent-fixture'}; agent.connected_agent = object(); await agent.agent_websocket(socket); socket.accept.assert_not_awaited()
    async def test_receive_acknowledges_pending_jobs_and_disconnect_fails_waiters(self):
        result = asyncio.get_running_loop().create_future(); waiter = asyncio.get_running_loop().create_future(); agent.pending_jobs.update({'r': result, 'other': waiter})
        socket = SimpleNamespace(headers={'authorization': 'Bearer agent-fixture'}, accept=AsyncMock(), receive_json=AsyncMock(side_effect=[{'type': 'ignored'}, {'type': 'job_result', 'job_id': 'r', 'ok': True}, WebSocketDisconnect()]))
        await agent.agent_websocket(socket); self.assertTrue((await result)['ok']); self.assertIsNone(agent.connected_agent)
        with self.assertRaises(ConnectionError): await waiter
    async def test_submit_auth_offline_success_send_failure_timeout_and_disconnect(self):
        job = agent.JobRequest(action='ping')
        with self.assertRaises(HTTPException) as error: await agent.submit_job(job, 'wrong')
        self.assertEqual(error.exception.status_code, 401)
        with self.assertRaises(HTTPException) as error: await agent.submit_job(job, 'controller-fixture')
        self.assertEqual(error.exception.status_code, 503)
        async def send(payload): agent.pending_jobs[payload['job_id']].set_result({'ok': True})
        agent.connected_agent = SimpleNamespace(send_json=AsyncMock(side_effect=send))
        self.assertEqual(await agent.submit_job(job, 'controller-fixture'), {'ok': True}); self.assertEqual(agent.pending_jobs, {})
        agent.connected_agent.send_json = AsyncMock(side_effect=OSError())
        with self.assertRaises(HTTPException) as error: await agent.submit_job(job, 'controller-fixture')
        self.assertEqual(error.exception.status_code, 503)
        agent.connected_agent.send_json = AsyncMock()
        for failure, status in ((TimeoutError(), 504), (ConnectionError(), 503)):
            async def fail(future, timeout): future.cancel(); raise failure
            with patch('app.routes.agent.asyncio.wait_for', side_effect=fail), self.assertRaises(HTTPException) as error: await agent.submit_job(job, 'controller-fixture')
            self.assertEqual(error.exception.status_code, status); self.assertEqual(agent.pending_jobs, {})
    async def test_health_endpoints(self):
        self.assertEqual((await health())['service'], 'public'); self.assertEqual((await admin_health())['service'], 'admin')


class TunnelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = patch.multiple(tunnel, _tunnel_process=None, _tunnel_url=None, _drain_task=None); self.state.start()
    async def asyncTearDown(self): await tunnel.stop_tunnel(); self.state.stop()
    def process(self, lines):
        return SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=lines)), returncode=0, terminate=MagicMock(), wait=AsyncMock(return_value=0), kill=MagicMock())
    async def test_captures_url_caches_connection_drains_output_and_stops(self):
        process = self.process([b'noise\n', b'visit https://test-name.trycloudflare.com\n', b'after\n', b''])
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process) as spawn:
            self.assertEqual(await tunnel.start_tunnel(), 'https://test-name.trycloudflare.com'); self.assertEqual(await tunnel.start_tunnel(), tunnel.get_tunnel_url()); spawn.assert_awaited_once()
            self.assertEqual(tunnel.tunnel_url, tunnel.get_tunnel_url())
            await tunnel._drain_task
        await tunnel.stop_tunnel(); self.assertIsNone(tunnel.get_tunnel_url()); process.terminate.assert_called_once()
        with self.assertRaises(AttributeError): getattr(tunnel, 'missing')
    async def test_ignores_the_cloudflare_api_host_in_log_lines(self):
        process = self.process([b'Post "https://api.trycloudflare.com/tunnel": dial failed\n', b'| https://real-name.trycloudflare.com |\n', b''])
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process):
            self.assertEqual(await tunnel.start_tunnel(), 'https://real-name.trycloudflare.com')
    async def test_the_tunnel_url_is_logged_where_the_console_shows_it(self):
        process = self.process([b'visit https://show-me.trycloudflare.com\n', b''])
        with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process), self.assertLogs('uvicorn.error', level='INFO') as logs:
            await tunnel.start_tunnel()
        self.assertIn('Cloudflare Tunnel active at: https://show-me.trycloudflare.com', ''.join(logs.output))

    async def test_missing_executable_missing_url_and_read_failure_are_cleaned(self):
        with patch('app.tunnel.asyncio.create_subprocess_exec', side_effect=FileNotFoundError), self.assertRaisesRegex(RuntimeError, 'not found'): await tunnel.start_tunnel()
        for lines in ([b''], [OSError('pipe failed')]):
            process = self.process(lines)
            with patch('app.tunnel.asyncio.create_subprocess_exec', return_value=process), self.assertRaisesRegex(RuntimeError, 'Failed to start'): await tunnel.start_tunnel()
            self.assertIsNone(tunnel._tunnel_process); process.terminate.assert_called_once()
    async def test_stop_cancels_drain_and_kills_after_timeout_and_tolerates_exit(self):
        tunnel._drain_task = asyncio.create_task(asyncio.sleep(10))
        process = self.process([]); process.wait = AsyncMock(side_effect=[TimeoutError(), 0]); tunnel._tunnel_process = process
        await tunnel.stop_tunnel(); process.kill.assert_called_once(); self.assertIsNone(tunnel._drain_task)
        for failure in (ProcessLookupError(), OSError('fixture')):
            process = self.process([]); process.terminate = MagicMock(side_effect=failure); tunnel._tunnel_process = process; await tunnel.stop_tunnel(); self.assertIsNone(tunnel._tunnel_process)
