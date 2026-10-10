
import hashlib
import json
import os
import platform
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

VERSION = "0.15.2"
CACHE_RECEIPT = ".janus-verified.json"


def toolchain_digest(root: Path) -> str:
    """Cover the compiler and its support files, without following external links."""
    if root.is_symlink():
        raise RuntimeError("Cached toolchain root must not be a symlink")
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative == CACHE_RECEIPT:
            continue
        digest.update(relative.encode("utf-8") + b"\0")
        if path.is_symlink():
            if not path.resolve().is_relative_to(root.resolve()):
                raise RuntimeError("Cached toolchain contains an external link")
            digest.update(b"link\0" + str(path.readlink()).encode("utf-8"))
        elif path.is_file():
            with path.open("rb") as file:
                while data := file.read(1024 * 1024):
                    digest.update(data)
        else:
            digest.update(b"directory")
    return digest.hexdigest()


def cache_verified(root: Path, release_hash: str) -> bool:
    try:
        record = root / CACHE_RECEIPT
        if record.is_symlink() or record.stat().st_size > 4096:
            return False
        receipt = json.loads(record.read_text())
        return (receipt["version"] == VERSION and receipt["release_sha256"] == release_hash
                and receipt["toolchain_sha256"] == toolchain_digest(root))
    except (OSError, ValueError, KeyError, TypeError, RuntimeError):
        return False

# Official SHA-256 checksums from ziglang.org
RELEASES = {
    ("Darwin", "x86_64"): (
        "x86_64-macos",
        "375b6909fc1495d16fc2c7db9538f707456bfc3373b14ee83fdd3e22b3d43f7f",
    ),
    ("Darwin", "arm64"): (
        "aarch64-macos",
        "3cc2bab367e185cdfb27501c4b30b1b0653c28d9f73df8dc91488e66ece5fa6b",
    ),
    ("Windows", "AMD64"): (
        "x86_64-windows",
        "3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c",
    ),
    ("Windows", "ARM64"): (
        "aarch64-windows",
        "b926465f8872bf983422257cd9ec248bb2b270996fbe8d57872cca13b56fc370",
    ),
    ("Windows", "x86"): (
        "x86-windows",
        "7a6dfc00f4cc09ec46d3e10eb06f42538e92b6285e34debea7462edaf371da98",
    ),
}


def install_zig() -> Path:
    system = platform.system()
    machine = platform.machine()
    key = (system, machine)

    if key not in RELEASES:
        raise RuntimeError(
            f"Unsupported platform: {system}/{machine}"
        )

    target, expected_hash = RELEASES[key]

    suffix = ".zip" if system == "Windows" else ".tar.xz"
    folder_name = f"zig-{target}-{VERSION}"

    url = (
        f"https://ziglang.org/download/"
        f"{VERSION}/{folder_name}{suffix}"
    )

    # Application-specific installation location
    if system == "Darwin":
        root = (
            Path.home()
            / "Library"
            / "Application Support"
            / "HackNC2026"
            / "toolchains"
        )
    else:
        root = (
            Path(
                os.environ.get(
                    "LOCALAPPDATA",
                    Path.home() / "AppData" / "Local",
                )
            )
            / "HackNC2026"
            / "toolchains"
        )

    root.mkdir(parents=True, exist_ok=True)

    destination = root / folder_name
    executable = "zig.exe" if system == "Windows" else "zig"
    compiler = destination / executable

    # Reuse previous installation
    if compiler.is_file() and cache_verified(destination, expected_hash):
        return compiler

    print(f"Installing Zig {VERSION} locally...")

    try:
        with tempfile.TemporaryDirectory(
            dir=root,
            prefix="zig-install-",
        ) as tmp:

            stage = Path(tmp)
            archive = stage / f"download{suffix}"

            digest = hashlib.sha256()
            size = 0

            # Download compiler
            with urllib.request.urlopen(
                url, timeout=60
            ) as response, archive.open("wb") as file:

                while block := response.read(1024 * 1024):
                    size += len(block)

                    if size > 128 * 1024 * 1024:
                        raise RuntimeError(
                            "Toolchain download too large"
                        )

                    digest.update(block)
                    file.write(block)

            # Verify downloaded archive
            if digest.hexdigest() != expected_hash:
                raise RuntimeError(
                    "Zig SHA-256 verification failed"
                )

            # Extract compiler
            if system == "Darwin":
                with tarfile.open(archive, "r:xz") as tar:
                    tar.extractall(stage, filter="data")
            else:
                with zipfile.ZipFile(archive) as zip_file:
                    zip_file.extractall(stage)

            extracted = stage / folder_name

            if not (extracted / executable).is_file():
                raise RuntimeError(
                    "Compiler not found in archive"
                )

            verified_tree = toolchain_digest(extracted)
            if destination.exists():
                # Older installations have no receipt. Re-verify them against
                # the checksum-verified release, preserving existing files.
                if toolchain_digest(destination) != verified_tree:
                    raise RuntimeError("Cached compiler integrity check failed; configure a trusted system compiler or replace the cache")
            else:
                extracted.rename(destination)
            with tempfile.NamedTemporaryFile(mode="w", dir=destination, prefix=".janus-receipt-", delete=False) as record:
                json.dump({"version": VERSION, "release_sha256": expected_hash,
                           "toolchain_sha256": verified_tree}, record)
                record_path = Path(record.name)
            try:
                record_path.replace(destination / CACHE_RECEIPT)
            finally:
                record_path.unlink(missing_ok=True)

    except (
        OSError,
        EOFError,
        tarfile.TarError,
        zipfile.BadZipFile,
    ) as exc:
        raise RuntimeError(
            f"Could not install Zig: {exc}"
        ) from exc

    print(f"Installed compiler: {compiler}")

    return compiler
