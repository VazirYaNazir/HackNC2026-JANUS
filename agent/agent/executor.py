
import subprocess
import tempfile

from dataclasses import dataclass
from pathlib import Path

from .compiler import CCompiler


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    returncode: int


class ExecutionError(RuntimeError):
    pass


def execute_c(
    source_code: str,
    compiler: CCompiler,
    stdin_data: str = "",
    timeout: int = 5,
) -> ExecutionResult:

    # Create isolated temporary build directory
    with tempfile.TemporaryDirectory(
        prefix="hacknc_"
    ) as temp:

        directory = Path(temp)

        source = directory / "program.c"

        executable = directory / (
            "program.exe"
            if compiler.system == "Windows"
            else "program"
        )

        # Write C source code
        source.write_text(
            source_code,
            encoding="utf-8",
        )

        # Compile using detected compiler
        compiler.compile(source, executable)

        # Execute the compiled program
        try:
            process = subprocess.run(
                [str(executable)],
                input=stdin_data,
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        except subprocess.TimeoutExpired as exc:
            raise ExecutionError(
                "Program execution timed out"
            ) from exc

        return ExecutionResult(
            stdout=process.stdout,
            stderr=process.stderr,
            returncode=process.returncode,
        )
