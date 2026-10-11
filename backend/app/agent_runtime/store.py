"""SQLite persistence through a bounded worker pool; sockets never perform SQL."""
import asyncio
import json
import sqlite3
import time
import secrets
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from uuid import uuid4
from typing import Any, Callable, TypeVar

from app import db
from .config import DeviceRegistry, Identity
from .models import GeneratedProgram, GenerationRequest

T = TypeVar("T")
JSON = dict[str, Any]


def scope_key(request: GenerationRequest) -> str:
    values = request.model_dump(exclude={"description"})
    for name in ("capabilities", "dependencies"):
        values[name] = sorted(values[name])
    return db.hash_token(json.dumps(values, sort_keys=True, separators=(",", ":")))


class SQLiteStore:
    def __init__(self) -> None:
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="janus-db")
        self.slots = asyncio.Semaphore(16)

    async def call(self, function: Callable[..., T], *args: Any) -> T:
        async with self.slots:
            future = asyncio.get_running_loop().run_in_executor(self.pool, partial(function, *args))
            try:
                return await asyncio.shield(future)
            except asyncio.CancelledError:
                while not future.done():
                    try:
                        await asyncio.shield(future)
                    except asyncio.CancelledError:
                        continue
                future.result()
                raise

    async def initialize(self, registry: DeviceRegistry) -> DeviceRegistry:
        def initialize():
            db.init_db()
            with db.get_db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                for device_id, identity in registry.devices.items():
                    old = connection.execute("SELECT owner_id FROM agent_devices WHERE device_id=?", (device_id,)).fetchone()
                    if old and old["owner_id"] != identity.owner_id:
                        raise ValueError("Cannot reassign an existing laptop to another owner")
                    connection.execute("""INSERT INTO agent_devices VALUES(?,?,?,?,?,?)
                        ON CONFLICT(device_id) DO UPDATE SET agent_hash=excluded.agent_hash,
                        controller_hash=excluded.controller_hash""",
                        (device_id, identity.owner_id, device_id, registry.agent_hashes[device_id], registry.controller_hashes[device_id], time.time()))
                rows = connection.execute("SELECT * FROM agent_devices").fetchall()
                hashes = [bytes(row[key]) for row in rows for key in ("agent_hash", "controller_hash")]
                if len(rows) > 128 or len(hashes) != len(set(hashes)):
                    raise ValueError("Invalid persisted agent registrations")
                # Interrupted work is recorded, never resumed or executed twice.
                for row in connection.execute("SELECT job_id, view_json FROM agent_jobs WHERE terminal=0").fetchall():
                    view = json.loads(row["view_json"])
                    view.update(state="INTERRUPTED", reason="BACKEND_RESTART", cancellation_confirmed=False)
                    connection.execute("UPDATE agent_jobs SET terminal=1, view_json=? WHERE job_id=?", (json.dumps(view), row["job_id"]))
                connection.commit()
            result = DeviceRegistry([])
            for row in rows:
                result.devices[row["device_id"]] = Identity(row["device_id"], row["owner_id"])
                result.agent_hashes[row["device_id"]] = bytes(row["agent_hash"])
                result.controller_hashes[row["device_id"]] = bytes(row["controller_hash"])
            return result
        try:
            return await self.call(initialize)
        except (sqlite3.Error, ValueError):
            raise ValueError("Cannot initialize registered agent database") from None

    async def create_job(self, identity: Identity, request: GenerationRequest, view: JSON) -> bool:
        def create():
            with db.get_db() as connection:
                connection.execute("INSERT INTO agent_jobs VALUES(?,?,?,?,?,?,?)", (view["job_id"], identity.device_id, identity.owner_id, request.model_dump_json(), json.dumps(view), 0, time.time()))
                connection.commit()
        try:
            await self.call(create)
        except sqlite3.IntegrityError:
            return False
        return True

    async def save_job(self, view: JSON, terminal: bool) -> None:
        def save():
            with db.get_db() as connection:
                connection.execute("UPDATE agent_jobs SET view_json=?, terminal=? WHERE job_id=? AND device_id=?", (json.dumps(view), int(terminal), view["job_id"], view["device_id"]))
                connection.commit()
        await self.call(save)

    async def job(self, identity: Identity, job_id: str) -> JSON | None:
        def get():
            with db.get_db() as connection:
                row = connection.execute("SELECT view_json FROM agent_jobs WHERE job_id=? AND device_id=? AND owner_id=?", (job_id, identity.device_id, identity.owner_id)).fetchone()
                return json.loads(row[0]) if row else None
        return await self.call(get)

    async def history(self, identity: Identity) -> list[JSON]:
        def get():
            with db.get_db() as connection:
                return [json.loads(row[0]) for row in connection.execute("SELECT view_json FROM agent_jobs WHERE device_id=? AND owner_id=? ORDER BY created_at DESC LIMIT 50", (identity.device_id, identity.owner_id))]
        return await self.call(get)

    async def save_program(self, identity: Identity, request: GenerationRequest, program: GeneratedProgram) -> str:
        program_id = uuid4().hex
        def save():
            with db.get_db() as connection:
                connection.execute("INSERT INTO generated_programs VALUES(?,?,?,?,?,?,?)", (program_id, identity.device_id, identity.owner_id, scope_key(request), request.model_dump_json(), program.model_dump_json(), time.time()))
                connection.commit()
        await self.call(save)
        return program_id

    async def programs(self, identity: Identity, request: GenerationRequest) -> list[JSON]:
        def get():
            with db.get_db() as connection:
                return [dict(row) for row in connection.execute("SELECT id,request_json,program_json FROM generated_programs WHERE device_id=? AND owner_id=? AND scope_key=? ORDER BY created_at DESC LIMIT 50", (identity.device_id, identity.owner_id, scope_key(request)))]
        return await self.call(get)

    async def program(self, identity: Identity, program_id: str, request: GenerationRequest) -> GeneratedProgram | None:
        def get():
            with db.get_db() as connection:
                row = connection.execute("SELECT program_json FROM generated_programs WHERE id=? AND device_id=? AND owner_id=? AND scope_key=?", (program_id, identity.device_id, identity.owner_id, scope_key(request))).fetchone()
                return dict(row) if row else None
        row = await self.call(get)
        if row is None:
            return None
        program = GeneratedProgram.model_validate_json(row["program_json"])
        program.check_scope(request)
        return program

    async def layout(self, identity: Identity) -> JSON:
        def get():
            with db.get_db() as connection:
                row = connection.execute("SELECT layout_json, revision FROM panel_layouts WHERE device_id=?", (identity.device_id,)).fetchone()
                return {"layout": json.loads(row[0]), "revision": row[1]} if row else {"layout": {"schemaVersion": 2, "coordinateSpace": "logical", "canvas": {"width": 360, "height": 780}, "name": identity.device_id, "widgets": []}, "revision": 0}
        return await self.call(get)

    async def save_layout(self, identity: Identity, layout: JSON, expected_revision: int) -> int | None:
        def save():
            with db.get_db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT revision FROM panel_layouts WHERE device_id=?", (identity.device_id,)).fetchone()
                if (row[0] if row else 0) != expected_revision:
                    return None
                revision = expected_revision + 1
                connection.execute("INSERT INTO panel_layouts VALUES(?,?,?) ON CONFLICT(device_id) DO UPDATE SET layout_json=excluded.layout_json,revision=excluded.revision", (identity.device_id, json.dumps(layout), revision))
                connection.commit()
                return revision
        return await self.call(save)

    async def action(self, identity: Identity, action_id: str, request: GenerationRequest | None = None) -> GenerationRequest | None:
        def action():
            with db.get_db() as connection:
                if request is not None:
                    connection.execute("INSERT INTO panel_actions VALUES(?,?,?) ON CONFLICT(device_id,action_id) DO UPDATE SET request_json=excluded.request_json", (identity.device_id, action_id, request.model_dump_json()))
                    connection.commit()
                row = connection.execute("SELECT request_json FROM panel_actions WHERE device_id=? AND action_id=?", (identity.device_id, action_id)).fetchone()
                return GenerationRequest.model_validate_json(row[0]) if row else None
        return await self.call(action)

    async def pairing(self, identity: Identity) -> str:
        code = secrets.token_hex(6).upper()
        def create():
            with db.get_db() as connection:
                connection.execute("DELETE FROM panel_pairing WHERE expires_at < ?", (time.time(),))
                connection.execute("INSERT INTO panel_pairing VALUES(?,?,?)", (code, identity.device_id, time.time() + 60))
                connection.commit()
        await self.call(create)
        return code

    async def pair(self, code: str, name: str) -> JSON | None:
        def pair():
            with db.get_db() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT p.device_id, p.expires_at, a.name FROM panel_pairing p JOIN agent_devices a ON a.device_id=p.device_id WHERE p.code=?", (code.strip().upper(),)).fetchone()
                connection.execute("DELETE FROM panel_pairing WHERE code=?", (code.strip().upper(),))
                if row is None or row["expires_at"] <= time.time():
                    connection.commit()
                    return None
                phone_id, token, now = "dev_" + uuid4().hex, "pwa_" + secrets.token_urlsafe(32), time.time()
                connection.execute("INSERT INTO devices VALUES(?,?,?,?,?)", (phone_id, name, db.hash_token(token), now, now))
                connection.execute("INSERT INTO panel_clients VALUES(?,?)", (phone_id, row["device_id"]))
                connection.commit()
                return {"deviceId": phone_id, "agentDeviceId": row["device_id"], "token": token, "pcName": row["name"]}
        return await self.call(pair)

    async def phone(self, token: str) -> Identity | None:
        def get():
            with db.get_db() as connection:
                row = connection.execute("SELECT a.device_id, a.owner_id FROM devices d JOIN panel_clients p ON p.phone_id=d.device_id JOIN agent_devices a ON a.device_id=p.device_id WHERE d.token=?", (db.hash_token(token),)).fetchone()
                if row:
                    connection.execute("UPDATE devices SET last_used=? WHERE token=?", (time.time(), db.hash_token(token)))
                    connection.commit()
                return Identity(row[0], row[1]) if row else None
        return await self.call(get)

    async def close(self) -> None:
        await asyncio.to_thread(self.pool.shutdown, wait=True)
