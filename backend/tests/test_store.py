import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db
from app.agent_runtime.config import DeviceRegistry, Identity, Registration
from app.agent_runtime.store import SQLiteStore, scope_key
from tests.support import program, registry, request


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix="janus-test-db-")
        self.path = patch.object(db, "DB_PATH", Path(self.folder.name) / "app.db")
        self.path.start()
        self.store = SQLiteStore()
        self.registered = await self.store.initialize(registry())
        self.identity = Identity("device-1", "owner-1")
        self.other = Identity("device-2", "owner-2")

    async def asyncTearDown(self):
        await self.store.close()
        self.path.stop()
        self.folder.cleanup()

    def view(self, job_id="stored"):
        return {"job_id": job_id, "device_id": "device-1", "state": "QUEUED", "reason": None, "result": None, "cancellation_confirmed": False}

    async def test_additive_migration_keeps_legacy_scripts_and_hashes_credentials(self):
        await self.store.call(db.save_script, "script", "Legacy", "print(1)", "button", "Run")
        loaded = await self.store.initialize(DeviceRegistry([]))
        self.assertEqual(len(loaded.devices), 2)
        self.assertEqual(loaded.agent("device-1", "agent-1-" + "a" * 32), self.identity)
        self.assertEqual(loaded.controller("control-1-" + "b" * 32), self.identity)
        self.assertEqual((await self.store.call(db.get_script_by_id, "script"))["code"], "print(1)")
        with db.get_db() as connection:
            self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            row = connection.execute("SELECT agent_hash, controller_hash FROM agent_devices LIMIT 1").fetchone()
            self.assertEqual(len(row[0]), 32)
            self.assertNotIn(b"agent-1", row[0])

    async def test_owner_reassignment_and_cross_role_credentials_are_rejected_atomically(self):
        changed = DeviceRegistry([Registration(device_id="device-1", owner_id="attacker", agent_token="a" * 32, controller_token="b" * 32)])
        with self.assertRaisesRegex(ValueError, "Cannot initialize"):
            await self.store.initialize(changed)
        collision = DeviceRegistry([Registration(device_id="device-3", owner_id="owner-3", agent_token="control-1-" + "b" * 32, controller_token="c" * 32)])
        with self.assertRaisesRegex(ValueError, "Cannot initialize"):
            await self.store.initialize(collision)
        self.assertEqual(len((await self.store.initialize(DeviceRegistry([]))).devices), 2)

    async def test_jobs_survive_restart_without_replay_and_results_are_scoped(self):
        self.assertTrue(await self.store.create_job(self.identity, request(), self.view()))
        self.assertFalse(await self.store.create_job(self.other, request(), self.view()))
        self.assertIsNone(await self.store.job(self.other, "stored"))
        self.assertEqual(await self.store.history(self.other), [])
        await self.store.initialize(DeviceRegistry([]))
        view = await self.store.job(self.identity, "stored")
        self.assertEqual((view["state"], view["reason"]), ("INTERRUPTED", "BACKEND_RESTART"))
        self.assertFalse(view["cancellation_confirmed"])
        view.update(state="COMPLETED", result={"status": "COMPLETED"})
        await self.store.save_job(view, True)
        await self.store.save_job({**view, "device_id": "device-2", "state": "BAD"}, True)
        await self.store.initialize(DeviceRegistry([]))
        self.assertEqual((await self.store.history(self.identity))[0]["state"], "COMPLETED")

    async def test_layout_revision_is_atomic_and_actions_are_device_scoped(self):
        empty = await self.store.layout(self.identity)
        self.assertEqual(empty["revision"], 0)
        layout = empty["layout"]
        results = await asyncio.gather(*(self.store.save_layout(self.identity, layout, 0) for _ in range(2)))
        self.assertCountEqual(results, [1, None])
        self.assertEqual((await self.store.layout(self.identity))["revision"], 1)
        self.assertEqual((await self.store.layout(self.other))["revision"], 0)
        self.assertIsNone(await self.store.action(self.identity, "compute"))
        await self.store.action(self.identity, "compute", request())
        await self.store.action(self.identity, "compute", request("updated"))
        self.assertEqual((await self.store.action(self.identity, "compute")).description, "updated")
        self.assertIsNone(await self.store.action(self.other, "compute"))

    async def test_programs_are_persisted_and_never_cross_device_or_metadata_scope(self):
        identifier = await self.store.save_program(self.identity, request(), program())
        self.assertEqual(len(await self.store.programs(self.identity, request())), 1)
        self.assertEqual(await self.store.programs(self.other, request()), [])
        self.assertEqual(await self.store.program(self.identity, identifier, request()), program())
        self.assertIsNone(await self.store.program(self.other, identifier, request()))
        different = request().model_copy(update={"capabilities": ["files.read"]})
        self.assertIsNone(await self.store.program(self.identity, identifier, different))
        order = different.model_copy(update={"capabilities": ["files.read", "network"]})
        self.assertEqual(scope_key(order), scope_key(order.model_copy(update={"capabilities": ["network", "files.read"]})))

    async def test_pairing_is_atomic_single_use_expiring_and_revocable(self):
        code = await self.store.pairing(self.identity)
        first, second = await asyncio.gather(self.store.pair(code.lower(), "Phone"), self.store.pair(code, "Other phone"))
        self.assertEqual(sum(value is not None for value in (first, second)), 1)
        paired = first or second
        self.assertEqual(await self.store.phone(paired["token"]), self.identity)
        self.assertIsNone(await self.store.phone("invalid"))
        with db.get_db() as connection:
            row = connection.execute("SELECT token FROM devices WHERE device_id=?", (paired["deviceId"],)).fetchone()
            self.assertEqual(row[0], db.hash_token(paired["token"]))
        await self.store.call(db.revoke_device_by_id, paired["deviceId"])
        self.assertIsNone(await self.store.phone(paired["token"]))
        expired = await self.store.pairing(self.identity)
        with db.get_db() as connection:
            connection.execute("UPDATE panel_pairing SET expires_at=0 WHERE code=?", (expired,)); connection.commit()
        self.assertIsNone(await self.store.pair(expired, "Phone"))
        self.assertIsNone(await self.store.pair("unknown", "Phone"))

    async def test_blocked_database_work_leaves_event_loop_responsive_and_cancellation_owned(self):
        started, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def blocking():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return "finished"
        worker = asyncio.create_task(self.store.call(blocking))
        await asyncio.wait_for(started.wait(), 1)
        worker.cancel()
        await asyncio.sleep(0)
        worker.cancel()
        await asyncio.sleep(0)
        self.assertFalse(worker.done())
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await worker

    async def test_existing_db_pairing_device_and_script_crud_remains_functional(self):
        code = await self.store.call(db.generate_pairing_code)
        results = await asyncio.gather(*(self.store.call(db.verify_and_consume_code, code.lower()) for _ in range(2)))
        self.assertCountEqual(results, [True, False])
        self.assertFalse(await self.store.call(db.verify_and_consume_code, None))
        self.assertFalse(await self.store.call(db.verify_and_consume_code, 1))
        expired = await self.store.call(db.generate_pairing_code, -1)
        self.assertFalse(await self.store.call(db.verify_and_consume_code, expired))
        phone = await self.store.call(db.register_device, "Old phone")
        self.assertEqual((await self.store.call(db.validate_device_token, phone["token"]))["name"], "Old phone")
        self.assertIsNone(await self.store.call(db.validate_device_token, "invalid"))
        self.assertTrue((await self.store.call(db.get_all_devices))[0]["is_active"])
        self.assertFalse(await self.store.call(db.revoke_device_by_id, "missing"))
        await self.store.call(db.save_script, "s", "Script", "print(1)", "button", "Run", "1")
        self.assertEqual((await self.store.call(db.get_all_scripts))[0]["widget"]["default_val"], "1")
        self.assertIsNone(await self.store.call(db.get_script_by_id, "missing"))
        self.assertTrue(await self.store.call(db.delete_script_by_id, "s"))
        self.assertFalse(await self.store.call(db.delete_script_by_id, "s"))
