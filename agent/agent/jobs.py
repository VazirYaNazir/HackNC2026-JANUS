"""Connection-scoped validation jobs and backend repair correlation."""
import asyncio

from .executor import ExecutionResult, execute_artifact
from .isolation import IsolationBoundary
from .validation import ID, ValidationResult, validate_job

JOB_MESSAGES = frozenset({"validate_job", "code_revision", "execute_job", "cancel_job"})


class ValidationService:
    """Local app configuration and replay ledger survive backend reconnects."""
    def __init__(self, compiler, policies=None, boundary=None, *, revision_timeout=30, local_platform=None):
        self.compiler = compiler
        self.policies = dict(policies or {})
        self.boundary = boundary or IsolationBoundary()
        self.revision_timeout = revision_timeout
        self.local_platform = local_platform
        self.seen_jobs = set()
        self.compiling = asyncio.Semaphore(1)

    def connect(self, outbox):
        return ValidationSession(self, outbox)


class ValidationSession:
    def __init__(self, service, outbox):
        self.service, self.outbox = service, outbox
        self.tasks, self.results, self.revisions = {}, {}, {}
        self.closed = False

    async def emit(self, event):
        if not self.closed:
            await self.outbox.result(event)

    async def reject(self, job_id, reason):
        await self.emit(ValidationResult(job_id, "REJECTED", reason).event())

    async def handle(self, message):
        if self.closed:
            return
        job_id = message.get("job_id")
        if not isinstance(job_id, str) or not ID.fullmatch(job_id):
            await self.reject(None, "SOURCE_INVALID")
            return
        kind = message.get("type")
        if kind == "code_revision":
            pending = self.revisions.get(job_id)
            if pending is None or pending.done():
                await self.reject(job_id, "STALE_REVISION")
            else:
                # validate_job checks the complete response and expected attempt.
                pending.set_result(message)
            return
        if kind == "cancel_job":
            if set(message) != {"type", "job_id"}:
                await self.reject(job_id, "SOURCE_INVALID")
                return
            task = self.tasks.get(job_id)
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            result = self.results.pop(job_id, None)
            if result:
                result.close()
            await self.emit(ValidationResult(job_id, "CANCELLED", "CANCELLED").event())
            return
        if kind == "execute_job":
            if set(message) != {"type", "job_id", "source_sha256"}:
                await self.reject(job_id, "SOURCE_INVALID")
                return
            result = self.results.get(job_id)
            if result is None or message.get("source_sha256") != result.source_sha256:
                await self.emit(ExecutionResult("EXECUTION_BLOCKED", "VALIDATION_REQUIRED").event(job_id))
                return
            if job_id in self.tasks:
                await self.emit(ExecutionResult("EXECUTION_BLOCKED", "JOB_BUSY").event(job_id))
                return
            self.tasks[job_id] = asyncio.create_task(self.execute(job_id, result))
            return
        if kind != "validate_job":
            await self.reject(job_id, "SOURCE_INVALID")
            return
        if job_id in self.service.seen_jobs:
            await self.reject(job_id, "DUPLICATE_JOB")
            return
        if len(self.service.seen_jobs) >= 256 or len(self.tasks) + len(self.results) >= 4:
            await self.reject(job_id, "RESOURCE_LIMIT")
            return
        # Never evict IDs and accidentally permit replay of an approved side effect.
        self.service.seen_jobs.add(job_id)
        self.tasks[job_id] = asyncio.create_task(self.validate(job_id, message))

    async def request_revision(self, job_id, attempt, diagnostics):
        if self.closed:
            raise asyncio.CancelledError
        pending = asyncio.get_running_loop().create_future()
        self.revisions[job_id] = pending
        try:
            await self.emit(diagnostics)
            return await pending
        finally:
            self.revisions.pop(job_id, None)

    async def validate(self, job_id, message):
        result = None
        try:
            async with self.service.compiling:
                result = await validate_job(message, self.service.compiler, self.request_revision,
                                            policy=self.service.policies.get(job_id), boundary=self.service.boundary,
                                            revision_timeout=self.service.revision_timeout, local_platform=self.service.local_platform)
            if self.closed:
                result.close()
                return
            if result.status == "READY_FOR_EXECUTION":
                self.results[job_id] = result
            else:
                result.close()
            await self.emit(result.event())
        except asyncio.CancelledError:
            if result:
                result.close()
            raise
        except Exception:
            if result:
                result.close()
            await self.emit(ValidationResult(job_id, "VALIDATION_FAILED", "INTERNAL_ERROR").event())
        finally:
            self.tasks.pop(job_id, None)

    async def execute(self, job_id, result):
        try:
            policy = self.service.policies.get(job_id)
            if self.closed or policy is None:
                return
            outcome = await execute_artifact(result, self.service.boundary, policy, policy.execution_stdin)
            await self.emit(outcome.event(job_id, policy.share_outputs))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self.emit(ExecutionResult("EXECUTION_FAILED", "INTERNAL_ERROR").event(job_id))
        finally:
            result.close()
            self.results.pop(job_id, None)
            self.tasks.pop(job_id, None)

    async def close(self):
        self.closed = True
        for pending in self.revisions.values():
            pending.cancel()
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for result in self.results.values():
            result.close()
        self.tasks.clear()
        self.results.clear()
        self.revisions.clear()
