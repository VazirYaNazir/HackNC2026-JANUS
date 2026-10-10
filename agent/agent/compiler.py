# test
import platform
import shutil
import tempfile

from dataclasses import dataclass
from pathlib import Path

from .installer import install_zig
from .process import run_bounded


@dataclass(frozen=True)
class CompileResult:
    compiler: str
    returncode: int | None
    stdout: str
    stderr: str
    elapsed_ms: int
    executable_exists: bool
    reason: str | None = None

    @property
    def success(self) -> bool:
        return self.returncode == 0 and self.executable_exists and self.reason is None


class CompilerError(RuntimeError):
    def __init__(self, message, result: CompileResult | None = None):
        super().__init__(message)
        self.result = result


@dataclass(frozen=True)
class CCompiler:
    name: str
    path: str
    system: str

    def command(
        self,
        source: Path,
        output: Path,
        link_args: tuple[str, ...] = (),
    ) -> list[str]:

        if self.name == "msvc":
            # Microsoft Visual C++ compiler
            command = [
                self.path,
                "/nologo",
                "/TC",
                "/std:c11",
                str(source),
                f"/Fe:{output}",
                f"/Fo:{output.with_suffix('.obj')}",
            ]
            if link_args:
                command.extend(["/link", *link_args])

        else:
            command = [self.path]

            if self.name == "zig":
                command.append("cc")

            command.extend([
                "-std=c11",
                "-O2",
                str(source),
                "-o",
                str(output),
            ])
            command.extend(link_args)


        return command

    def compile_detailed(self, source: Path, output: Path, timeout: int = 30,
                         link_args: tuple[str, ...] = (), diagnostic_limit: int = 65536) -> CompileResult:
        """Trusted source/toolchain path. Generated source must use a verified build boundary."""
        output.unlink(missing_ok=True)
        result = run_bounded(self.command(source, output, link_args), cwd=source.parent,
                             timeout=timeout, stdout_limit=diagnostic_limit, stderr_limit=diagnostic_limit)
        exists = output.is_file() and not output.is_symlink()
        reason = ("COMPILER_UNAVAILABLE" if result.unavailable else "COMPILE_TIMEOUT" if result.timed_out
                  else "RESOURCE_LIMIT" if result.output_limited else "COMPILE_ERROR" if result.returncode != 0
                  else "COMPILER_UNAVAILABLE" if not exists else None)
        if reason:
            output.unlink(missing_ok=True)
        return CompileResult(self.name, result.returncode, result.stdout, result.stderr,
                             result.elapsed_ms, exists, reason)

    def compile(self, source: Path, output: Path, timeout: int = 30,
                link_args: tuple[str, ...] = ()) -> CompileResult:
        result = self.compile_detailed(source, output, timeout, link_args)
        if not result.success:
            raise CompilerError(f"Compilation failed ({result.reason}):\n{result.stderr or result.stdout}", result)
        return result


def detect_compiler() -> CCompiler:
    system = platform.system()

    if system == "Darwin":
        candidates = [
            ("clang", "clang"),
            ("gcc", "gcc"),
        ]

    elif system == "Windows":
        candidates = [
            ("clang", "clang"),
            ("gcc", "gcc"),
            ("msvc", "cl"),
        ]

    else:
        raise CompilerError(
            f"Unsupported operating system: {system}"
        )

    errors = []

    for name, command in candidates:
        path = shutil.which(command)

        if path is None:
            continue

        compiler = CCompiler(
            name=name,
            path=path,
            system=system,
        )

        # Verify the compiler can actually build a program
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)

            source = directory / "test.c"

            executable = directory / (
                "test.exe" if system == "Windows"
                else "test"
            )

            source.write_text(
                "int main(void) { return 0; }\n",
                encoding="utf-8",
            )

            try:
                compiler.compile(source, executable)
                return compiler

            except (CompilerError, OSError) as error:
                errors.append(f"{name}: {error}")

    details = "\n".join(errors)


    # No working system compiler found.
    # Install a portable compiler automatically.

    try:
        zig_path = install_zig()

        compiler = CCompiler(
            name="zig",
            path=str(zig_path),
            system=system,
        )

        # Verify that Zig can compile a real program
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)

            source = directory / "test.c"
            executable = directory / (
                "test.exe" if system == "Windows"
                else "test"
            )

            source.write_text(
                "int main(void) { return 0; }\n",
                encoding="utf-8",
            )

            compiler.compile(source, executable)

        return compiler

    except (RuntimeError, CompilerError, OSError) as error:
        raise CompilerError(
            f"Automatic compiler installation failed: {error}"
        ) from error
