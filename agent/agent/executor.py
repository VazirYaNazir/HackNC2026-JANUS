
"""Execute a verified artifact under a separate, local capability authorization."""
import asyncio
from dataclasses import dataclass

from .isolation import BoundaryUnavailable, ResourceLimitExceeded
from .validation import valid_output


@dataclass(frozen=True)
class ExecutionResult:
    status: str
    reason: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""

    def event(self, job_id, share_outputs=False):
        event = {"type": "execution_result", "job_id": job_id, "status": self.status, "reason": self.reason}
        if share_outputs:
            event.update(returncode=self.returncode, stdout=self.stdout, stderr=self.stderr)
        return event


class ExecutionError(RuntimeError):
    pass


def execute_c(*args, **kwargs):
    """Deliberately removed unsafe API; source text is never executed directly."""
    raise ExecutionError("Use validate_job and execute_artifact with a verified isolation boundary")


async def execute_artifact(validation, boundary, policy, stdin_data=""):
    artifact = validation.artifact
    if validation.status != "READY_FOR_EXECUTION" or validation.preflight not in ("PREFLIGHT_PASSED", "PREFLIGHT_LIMITED") or artifact is None:
        return ExecutionResult("EXECUTION_BLOCKED", "VALIDATION_REQUIRED")
    if policy != artifact.policy or not policy.execute_authorized:
        return ExecutionResult("EXECUTION_BLOCKED", "AUTHORIZATION_REQUIRED")
    if artifact.job.operation != policy.operation or not artifact.job.capabilities <= policy.capabilities:
        return ExecutionResult("EXECUTION_BLOCKED", "CAPABILITY_DENIED")
    if not boundary.can_run(artifact.job.capabilities):
        return ExecutionResult("EXECUTION_BLOCKED", "EXECUTION_BOUNDARY_UNAVAILABLE")
    if not artifact.verify():
        return ExecutionResult("EXECUTION_BLOCKED", "ARTIFACT_INVALID_OR_CONSUMED")
    if not isinstance(stdin_data, str) or len(stdin_data.encode()) > policy.limits.stdout_bytes:
        return ExecutionResult("EXECUTION_BLOCKED", "INPUT_INVALID")
    # Authorization is single-use, even if execution fails or is cancelled.
    artifact.consumed = True
    try:
        result = await asyncio.wait_for(boundary.run(artifact.executable, stdin_data, policy.limits, artifact.job.capabilities), policy.limits.run_seconds)
        if not valid_output(result, policy):
            return ExecutionResult("EXECUTION_FAILED", "RESOURCE_OR_OUTPUT_LIMIT")
        return ExecutionResult("COMPLETED" if result.returncode == 0 else "EXECUTION_FAILED", None if result.returncode == 0 else "NONZERO_EXIT", result.returncode, result.stdout, result.stderr)
    except BoundaryUnavailable:
        return ExecutionResult("EXECUTION_BLOCKED", "EXECUTION_BOUNDARY_UNAVAILABLE")
    except TimeoutError:
        return ExecutionResult("EXECUTION_FAILED", "EXECUTION_TIMEOUT")
    except ResourceLimitExceeded:
        return ExecutionResult("EXECUTION_FAILED", "RESOURCE_LIMIT")
    except OSError:
        return ExecutionResult("EXECUTION_FAILED", "LOAD_FAILED")
