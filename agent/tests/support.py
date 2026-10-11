"""Shared fixtures: generated native code is never compiled or executed here."""
from agent.compiler import CCompiler, CompileResult
from agent.isolation import IsolationBoundary, REQUIRED_GUARANTEES
from agent.process import ProcessResult

SOURCE = "int main(void) { return 0; }"
COMPILER = CCompiler("clang", "/not-executed/clang", "Darwin")
LOCAL = ("Darwin", "arm64")


def job(job_id="job-123", source=SOURCE, **fields):
    return {"type": "validate_job", "job_id": job_id, "attempt": 1, "language": "c11", "source": source,
            "operation": "compute", "platform": "Darwin", "architecture": "arm64", "capabilities": [], "dependencies": [], **fields}


class SimulatedBoundary(IsolationBoundary):
    """Test-only worker replies; fake artifacts are deliberately not executables."""
    build_guarantees = REQUIRED_GUARANTEES
    run_guarantees = REQUIRED_GUARANTEES
    run_capabilities = frozenset({"files.write", "desktop.mouse"})

    def __init__(self, compilations=(), executions=()):
        self.compilations, self.executions = list(compilations), list(executions)
        self.sources, self.runs, self.directories = [], [], []

    async def compile(self, compiler, source, output, limits, link_args):
        self.sources.append(source.read_text())
        self.directories.append(source.parent)
        outcome = self.compilations.pop(0) if self.compilations else None
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome:
            return outcome if isinstance(outcome, CompileResult) else CompileResult("clang", 1, "", outcome, 1, False, "COMPILE_ERROR")
        output.write_bytes(b"simulated-not-executable")
        return CompileResult("clang", 0, "", "", 1, True)

    async def run(self, executable, stdin, limits, capabilities):
        self.runs.append((executable, stdin, capabilities))
        outcome = self.executions.pop(0) if self.executions else ProcessResult(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome
