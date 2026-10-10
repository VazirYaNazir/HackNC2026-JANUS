# test
import platform
import shutil
import subprocess
import tempfile

from dataclasses import dataclass
from pathlib import Path

from .installer import install_zig

class CompilerError(RuntimeError):
    pass


@dataclass(frozen=True)
class CCompiler:
    name: str
    path: str
    system: str

    def compile(
        self,
        source: Path,
        output: Path,
        timeout: int = 30,
        link_args: tuple[str, ...] = (),
    ) -> None:

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


        try:
            result = subprocess.run(
                command,
                cwd=source.parent,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

        except subprocess.TimeoutExpired as exc:
            raise CompilerError(
                "C compilation timed out"
            ) from exc

        if result.returncode != 0:
            raise CompilerError(
                f"Compilation failed:\n"
                f"{result.stderr or result.stdout}"
            )


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
