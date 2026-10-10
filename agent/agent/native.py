"""Build standalone C tools without a third-party native library."""
import hashlib
import json
import platform
from pathlib import Path

from .compiler import CCompiler, detect_compiler

TOOLS = ("volume", "mouse", "metrics", "processes", "screen")
ROOT = Path(__file__).resolve().parents[1]
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


def build_tools(names=TOOLS, compiler: CCompiler | None = None) -> dict[str, Path]:
    compiler = compiler or detect_compiler()
    output = ROOT / ".native-bin" / f"{compiler.system}-{platform.machine()}"
    output.mkdir(parents=True, exist_ok=True)
    binaries = {}
    for name in names:
        if name not in TOOLS:
            raise ValueError(f"Unknown native tool: {name}")
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
        try:
            cached = json.loads(metadata.read_text())
        except (OSError, ValueError):
            cached = None
        if not binary.exists() or cached != fingerprint:
            compiler.compile(source, binary, timeout=120, link_args=link_arguments(name, compiler))
            metadata.write_text(json.dumps(fingerprint))
        binaries[name] = binary
    return binaries
