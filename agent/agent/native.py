"""Build standalone C tools without a third-party native library."""
import hashlib
import json
import platform
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .compiler import CCompiler, CompilerError, detect_compiler

TOOLS = ("volume", "mouse", "metrics", "processes", "screen")
ROOT = Path(__file__).resolve().parents[1]
BUILD_LOCKS = {name: threading.Lock() for name in TOOLS}
MAC_FRAMEWORKS = {
    "volume": ("CoreAudio", "CoreFoundation"),
    "mouse": ("ApplicationServices",),
    "metrics": ("IOKit", "CoreFoundation"),
    "processes": (),
    "screen": ("CoreGraphics", "ImageIO", "CoreFoundation"),
}
WINDOWS_LIBS = {
    "volume": ("ole32", "uuid"),
    "mouse": ("user32",),
    "metrics": ("ole32", "oleaut32", "wbemuuid"),
    "processes": ("psapi",),
    "screen": ("ole32", "oleaut32", "windowscodecs", "gdi32", "user32", "uuid"),
}


def link_arguments(tool: str, compiler: CCompiler) -> tuple[str, ...]:
    if compiler.system == "Darwin":
        return tuple(arg for name in MAC_FRAMEWORKS[tool] for arg in ("-framework", name))
    if compiler.name == "msvc":
        return tuple(f"{lib}.lib" for lib in WINDOWS_LIBS[tool])
    return tuple(f"-l{lib}" for lib in WINDOWS_LIBS[tool])


def build_tools(names=TOOLS, compiler: CCompiler | None = None, *, workers=5) -> dict[str, Path]:
    names = tuple(dict.fromkeys(names))
    if any(name not in TOOLS for name in names):
        raise ValueError("Unknown native tool")
    if type(workers) is not int or not 1 <= workers <= len(TOOLS):
        raise ValueError("Build workers must be between 1 and 5")
    if not names:
        return {}
    compiler = compiler or detect_compiler()
    cancelled = threading.Event()
    output = ROOT / ".native-bin" / f"{compiler.system}-{platform.machine()}"
    output.mkdir(parents=True, exist_ok=True)

    def build_one(name):
        source = ROOT / "native" / f"{name}.c"
        binary = output / (f"{name}.exe" if compiler.system == "Windows" else name)
        metadata = binary.with_suffix(binary.suffix + ".build.json")
        compiler_stat = Path(compiler.path).stat()
        fingerprint = {
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "compiler": compiler.path,
            "compiler_mtime_ns": compiler_stat.st_mtime_ns,
            "compiler_size": compiler_stat.st_size,
            "link_args": list(link_arguments(name, compiler)),
        }
        # Different tools build concurrently. The same cache entry is published
        # once even when multiple threads call build_tools simultaneously.
        with BUILD_LOCKS[name]:
            if cancelled.is_set():
                raise CompilerError("Native build cancelled")
            try:
                cached = json.loads(metadata.read_text())
            except (OSError, ValueError):
                cached = None
            if not binary.is_file() or binary.is_symlink() or cached != fingerprint:
                # A failed build never replaces the last working executable or
                # publishes metadata for a partially written compiler output.
                with tempfile.TemporaryDirectory(dir=output, prefix=f"{name}-build-") as tmp:
                    staged = Path(tmp) / binary.name
                    compiler.compile(source, staged, timeout=120, link_args=link_arguments(name, compiler), cancel_event=cancelled)
                    if cancelled.is_set():
                        raise CompilerError("Native build cancelled")
                    if not staged.is_file() or staged.is_symlink():
                        raise OSError("Native build produced no regular executable")
                    staged_metadata = Path(tmp) / metadata.name
                    staged_metadata.write_text(json.dumps(fingerprint))
                    staged.replace(binary)
                    staged_metadata.replace(metadata)
            return name, binary

    pool = ThreadPoolExecutor(max_workers=min(workers, len(names)), thread_name_prefix="janus-build")
    try:
        futures = [pool.submit(build_one, name) for name in names]
        completed = {}
        for future in as_completed(futures):
            name, binary = future.result()
            completed[name] = binary
        return {name: completed[name] for name in names}
    except BaseException:
        cancelled.set()
        raise
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
