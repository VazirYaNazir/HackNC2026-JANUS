"""Event-driven generation -> local validation -> bounded repair -> execution."""
import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Identity, RuntimeSettings
from .connections import AgentConnection, ConnectionHub, PipelineError
from .llm import LLMProvider, ProviderError
from .models import AckPayload, Envelope, ExecutionPayload, GeneratedProgram, GenerationRequest, RepairPayload, ValidationPayload, source_hash

TERMINAL = frozenset({"COMPLETED", "REJECTED", "VALIDATION_FAILED", "VALIDATION_BLOCKED", "EXECUTION_FAILED", "EXECUTION_BLOCKED", "CANCELLED", "TIMED_OUT", "DISCONNECTED", "PROVIDER_ERROR", "FAILED"})


@dataclass
class JobRecord:
    job_id: str
    identity: Identity
    connection: AgentConnection
    request: GenerationRequest
    state: str = "QUEUED"
    reason: str | None = None
    program: GeneratedProgram | None = field(default=None, repr=False)
    program_id: str | None = None
    widget_id: str | None = None
    action_id: str | None = None
    source_sha256: str | None = None
    repairs: int = 0
    ack_count: int = 0
    result: dict[str, Any] | None = None
    created: float = field(default_factory=time.monotonic)
    finished: float | None = None
    events: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=16), repr=False)
    done: asyncio.Event = field(default_factory=asyncio.Event, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)
    cancel_confirmed: asyncio.Event = field(default_factory=asyncio.Event, repr=False)

    def view(self) -> dict[str, Any]:
        return {"job_id": self.job_id, "device_id": self.identity.device_id, "state": self.state,
                "reason": self.reason, "repairs": self.repairs, "acknowledgments": self.ack_count,
                "source_sha256": self.source_sha256, "result": self.result,
                "program_id": self.program_id, "widget_id": self.widget_id, "action_id": self.action_id,
                "cancellation_confirmed": self.cancel_confirmed.is_set(),
                "elapsed_ms": round(1000 * ((self.finished or time.monotonic()) - self.created), 3)}

    def finish(self, state: str, reason: str | None = None, result: dict[str, Any] | None = None) -> None:
        self.state, self.reason, self.result = state, reason, result
        self.finished = time.monotonic()
        self.done.set()


class JobManager:
    def __init__(self, hub: ConnectionHub, provider: LLMProvider, settings: RuntimeSettings, *, store=None, notify=None):
        self.hub, self.provider, self.settings = hub, provider, settings
        self.records: dict[str, JobRecord] = {}
        self.seen: dict[str, Identity] = {}
        self.closing = False
        self.admissions: dict[str, asyncio.Task] = {}
        self.store, self.notify = store, notify
        hub.on_event, hub.on_disconnect = self.receive, self.disconnected

    def get(self, identity: Identity, job_id: str) -> JobRecord:
        record = self.records.get(job_id)
        if record is None or record.identity != identity:
            raise PipelineError("JOB_NOT_FOUND", 404)
        return record

    async def submit(self, identity: Identity, device_id: str, job_id: str, request: GenerationRequest,
                     program: GeneratedProgram | None = None, *, widget_id=None, action_id=None) -> JobRecord:
        if identity.device_id != device_id:
            raise PipelineError("DEVICE_FORBIDDEN", 403)
        connection = self.hub.get(device_id)
        if (request.platform, request.architecture) != (connection.hello.platform, connection.hello.architecture):
            raise PipelineError("TARGET_MISMATCH", 422)
        if self.closing or (self.store is None and len(self.seen) >= 4096):
            raise PipelineError("SERVICE_CAPACITY", 503)
        if job_id in self.seen:
            raise PipelineError("DUPLICATE_JOB", 409)
        active = [item for item in self.records.values() if not item.done.is_set() or
                  (item.task is not None and not item.task.done())]
        per_device = sum(item.identity.device_id == device_id for item in active)
        capacity = self.settings.max_jobs_per_device
        if len(active) >= self.settings.max_active_jobs or per_device >= capacity:
            raise PipelineError("JOB_CAPACITY", 429)
        while len(self.records) >= self.settings.max_jobs:
            expired = next((key for key, value in self.records.items() if value.done.is_set() and
                            (value.task is None or value.task.done())), None)
            if expired is None:
                raise PipelineError("JOB_CAPACITY", 429)
            self.records.pop(expired)
        if program is not None:
            program.check_scope(request)
        record = JobRecord(job_id, identity, connection, request, program=program, widget_id=widget_id, action_id=action_id)
        self.records[job_id] = record
        if self.store is not None and len(self.seen) >= 4096:
            self.seen.pop(next(key for key in self.seen if key not in self.records))
        self.seen[job_id] = identity
        if self.store is not None:
            self.admissions[job_id] = asyncio.current_task()
            admission = asyncio.create_task(self.store.create_job(identity, request, record.view()))
            try:
                if not await asyncio.shield(admission):
                    raise PipelineError("DUPLICATE_JOB", 409)
            except asyncio.CancelledError:
                while not admission.done():
                    try:
                        await asyncio.shield(admission)
                    except asyncio.CancelledError:
                        continue
                if admission.result():
                    if not record.done.is_set():
                        record.finish("CANCELLED", "SUBMISSION_CANCELLED")
                    await self._publish(record)
                self.records.pop(job_id, None)
                self.seen.pop(job_id, None)
                raise
            except BaseException:
                self.records.pop(job_id, None)
                self.seen.pop(job_id, None)
                raise
            finally:
                self.admissions.pop(job_id, None)
        record.task = asyncio.create_task(self._run(record), name=f"agent-job:{job_id}")
        return record

    async def _run(self, record: JobRecord) -> None:
        try:
            await self._run_job(record)
        finally:
            if self.store is None:
                if self.notify is not None:
                    self.notify(record.identity, {"type": "job_update", "job": record.view()})
            else:
                publication = asyncio.create_task(self._publish(record))
                while not publication.done():
                    try:
                        await asyncio.shield(publication)
                    except asyncio.CancelledError:
                        continue
                publication.result()

    async def _publish(self, record):
        if self.store is not None:
            await self.store.save_job(record.view(), record.done.is_set())
        if self.notify is not None:
            self.notify(record.identity, {"type": "job_update", "job": record.view()})

    async def status(self, identity, job_id):
        if job_id in self.records:
            return self.get(identity, job_id).view()
        view = await self.store.job(identity, job_id) if self.store is not None else None
        if view is None:
            raise PipelineError("JOB_NOT_FOUND", 404)
        return view

    async def _run_job(self, record: JobRecord) -> None:
        try:
            if record.done.is_set():
                return
            async with asyncio.timeout(self.settings.job_timeout):
                if record.program is None:
                    record.state = "GENERATING"
                    await self._publish(record)
                    record.program = await self.provider.generate(record.request)
                record.program.check_scope(record.request)
                record.source_sha256 = source_hash(record.program.source)
                record.state = "VALIDATING"
                await self._publish(record)
                manifest = record.program.manifest(record.job_id)
                await record.connection.send("validate_job", record.job_id, record.state,
                                             {key: value for key, value in manifest.items() if key not in ("type", "job_id")})
                while True:
                    frame = await record.events.get()
                    if frame.type == "job_ack":
                        record.ack_count += 1
                        continue
                    if frame.type == "repair_requested":
                        repair = RepairPayload.model_validate(frame.payload)
                        if record.state != "VALIDATING" or repair.attempt != record.repairs + 1 or repair.source_sha256 != record.source_sha256 or (repair.platform, repair.architecture) != (record.request.platform, record.request.architecture):
                            raise ValueError("Invalid repair correlation")
                        if record.repairs >= 2:
                            raise ValueError("Repair limit exceeded")
                        record.state = "REPAIRING"
                        await self._publish(record)
                        revised = await self.provider.repair(record.request, record.program.source, repair.diagnostics)
                        revised.check_scope(record.request)
                        if source_hash(revised.source) == record.source_sha256:
                            raise ProviderError("REPEATED_SOURCE")
                        record.program = revised
                        record.repairs += 1
                        record.source_sha256 = source_hash(revised.source)
                        await record.connection.send("code_revision", record.job_id, "REPAIRING",
                                                     {"attempt": record.repairs + 1, "language": "c11", "source": revised.source})
                        record.state = "VALIDATING"
                        await self._publish(record)
                    elif frame.type == "validation_result":
                        result = ValidationPayload.model_validate(frame.payload)
                        if record.state != "VALIDATING":
                            raise ValueError("Unexpected validation state")
                        if result.status != "READY_FOR_EXECUTION":
                            record.finish(result.status, result.reason, frame.payload)
                            return
                        if result.source_sha256 != record.source_sha256 or result.preflight not in ("PREFLIGHT_PASSED", "PREFLIGHT_LIMITED"):
                            raise ValueError("Invalid ready artifact")
                        record.state = "EXECUTING"
                        if self.store is not None:
                            record.program_id = await self.store.save_program(record.identity, record.request, record.program)
                        await self._publish(record)
                        await record.connection.send("execute_job", record.job_id, record.state, {"source_sha256": record.source_sha256})
                    elif frame.type == "execution_result":
                        if record.state != "EXECUTING":
                            raise ValueError("Unexpected execution state")
                        result = ExecutionPayload.model_validate(frame.payload)
                        record.finish(result.status, result.reason, frame.payload)
                        return
        except asyncio.CancelledError:
            if not record.done.is_set():
                record.finish("CANCELLED", "CANCELLED")
            await self._cancel_local(record)
            raise
        except TimeoutError:
            record.finish("TIMED_OUT", "JOB_TIMEOUT")
            await self._cancel_local(record)
        except ProviderError as error:
            record.finish("PROVIDER_ERROR", error.reason)
            await self._cancel_local(record)
        except PipelineError as error:
            record.finish("DISCONNECTED", error.reason)
        except Exception:
            record.finish("FAILED", "PROTOCOL_OR_INTERNAL_ERROR")
            await self._cancel_local(record)

    async def _cancel_local(self, record: JobRecord) -> None:
        if not record.connection.closed:
            try:
                await record.connection.send("cancel_job", record.job_id, "CANCELLED", {})
                await asyncio.wait_for(record.cancel_confirmed.wait(), self.settings.send_timeout)
            except PipelineError:
                pass
            except TimeoutError:
                pass  # Cancellation delivery is not proof of local worker cleanup.

    async def receive(self, connection: AgentConnection, frame: Envelope) -> None:
        record = self.records.get(frame.job_id)
        known = self.seen.get(frame.job_id) == connection.identity
        if record is None and not known and self.store is not None:
            known = await self.store.job(connection.identity, frame.job_id) is not None
        if record is None and known and frame.type in ("job_ack", "repair_requested", "validation_result", "execution_result"):
            return  # Bounded ownership tombstones safely discard late evicted results.
        if record is None or record.connection is not connection or record.identity != connection.identity:
            raise ValueError("Unknown or incorrectly routed job")
        if frame.type == "validation_result" and ValidationPayload.model_validate(frame.payload).status == "CANCELLED":
            if frame.state != "CANCELLED":
                raise ValueError("Cancellation state mismatch")
            record.cancel_confirmed.set()
            if record.done.is_set():
                await self._publish(record)
                return
        if record.done.is_set():
            return  # Late cancellation/ack messages cannot resurrect terminal jobs.
        model = {"job_ack": AckPayload, "repair_requested": RepairPayload,
                 "validation_result": ValidationPayload, "execution_result": ExecutionPayload}.get(frame.type)
        if model is None:
            raise ValueError("Unsupported agent event")
        parsed = model.model_validate(frame.payload)
        expected_state = "ACCEPTED" if frame.type == "job_ack" else "REPAIRING" if frame.type == "repair_requested" else parsed.status
        if frame.state != expected_state:
            raise ValueError("Event state mismatch")
        try:
            record.events.put_nowait(frame)
        except asyncio.QueueFull:
            raise ValueError("Too many job events") from None

    async def wait(self, identity: Identity, job_id: str, timeout: float = 30) -> dict[str, Any]:
        if job_id not in self.records:
            return await self.status(identity, job_id)
        record = self.get(identity, job_id)
        try:
            await asyncio.wait_for(record.done.wait(), timeout)
        except TimeoutError:
            raise PipelineError("WAIT_TIMEOUT", 504) from None
        if self.store is not None and record.task is not None:
            await asyncio.gather(asyncio.shield(record.task), return_exceptions=True)
        return record.view()

    async def cancel(self, identity: Identity, job_id: str) -> dict[str, Any]:
        if job_id not in self.records:
            return await self.status(identity, job_id)
        record = self.get(identity, job_id)
        if not record.done.is_set():
            record.finish("CANCELLED", "CANCELLED")
            if record.task is not None:
                record.task.cancel()
                await asyncio.gather(record.task, return_exceptions=True)
        return record.view()

    async def disconnected(self, connection: AgentConnection) -> None:
        tasks = []
        for record in self.records.values():
            if record.connection is connection and not record.done.is_set():
                record.finish("DISCONNECTED", "AGENT_DISCONNECTED")
                if record.task is not None:
                    record.task.cancel()
                    tasks.append(record.task)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        self.closing = True
        admissions = list(self.admissions.values())
        for task in admissions:
            task.cancel()
        await asyncio.gather(*admissions, return_exceptions=True)
        tasks = []
        for record in self.records.values():
            if not record.done.is_set():
                record.finish("CANCELLED", "SERVER_SHUTDOWN")
                if record.task is not None:
                    record.task.cancel()
            if record.task is not None and not record.task.done():
                tasks.append(record.task)
        await asyncio.gather(*tasks, return_exceptions=True)
