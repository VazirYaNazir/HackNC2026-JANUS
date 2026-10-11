"""Unit coverage for retained admin/legacy APIs and tunnel lifecycle."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException, WebSocketDisconnect
from fastapi.testclient import TestClient
from app import db
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
