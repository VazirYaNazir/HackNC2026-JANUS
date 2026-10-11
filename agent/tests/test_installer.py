"""Real archive fixtures exercise verified installation without network access."""
from contextlib import ExitStack, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from agent.installer import CACHE_RECEIPT, RELEASES, VERSION, cache_verified, install_zig, toolchain_digest


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)

    def archive(self, system="Darwin", machine="arm64", *, include_compiler=True):
        target = RELEASES[(system, machine)][0]
        folder = f"zig-{target}-{VERSION}"
        files = {f"{folder}/lib/runtime.h": b"trusted fixture header"}
        if include_compiler:
            files[f"{folder}/{'zig.exe' if system == 'Windows' else 'zig'}"] = b"trusted fixture compiler"
        buffer = io.BytesIO()
        if system == "Windows":
            with zipfile.ZipFile(buffer, "w") as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
        else:
            with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
                for name, data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    info.mode = 0o755 if name.endswith("/zig") else 0o644
                    archive.addfile(info, io.BytesIO(data))
        return buffer.getvalue(), target

    def configured(self, data, system="Darwin", machine="arm64", *, expected=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        target = RELEASES[(system, machine)][0]
        expected = expected or hashlib.sha256(data).hexdigest()
        stack.enter_context(patch("agent.installer.platform.system", return_value=system))
        stack.enter_context(patch("agent.installer.platform.machine", return_value=machine))
        stack.enter_context(patch("agent.installer.Path.home", return_value=self.home))
        stack.enter_context(patch.dict(os.environ, {"LOCALAPPDATA": str(self.home / "local")}, clear=True))
        stack.enter_context(patch.dict("agent.installer.RELEASES", {(system, machine): (target, expected)}))
        opener = stack.enter_context(patch("agent.installer.urllib.request.urlopen", side_effect=lambda *args, **kwargs: io.BytesIO(data)))
        stack.enter_context(redirect_stdout(io.StringIO()))
        return opener, expected

    def test_each_supported_archive_installs_and_reuses_verified_payload(self):
        for system, machine in RELEASES:
            with self.subTest(system=system, machine=machine):
                data, target = self.archive(system, machine)
                opener, expected = self.configured(data, system, machine)
                compiler = install_zig()
                self.assertEqual(compiler.read_bytes(), b"trusted fixture compiler")
                self.assertEqual((compiler.parent / "lib/runtime.h").read_bytes(), b"trusted fixture header")
                self.assertTrue(cache_verified(compiler.parent, expected))
                self.assertEqual(install_zig(), compiler)
                opener.assert_called_once_with(f"https://ziglang.org/download/{VERSION}/zig-{target}-{VERSION}{'.zip' if system == 'Windows' else '.tar.xz'}", timeout=60)

    def test_archive_checksum_mismatch_leaves_no_executable(self):
        data, _ = self.archive()
        self.configured(data, expected="0" * 64)
        with self.assertRaisesRegex(RuntimeError, "SHA-256 verification failed"):
            install_zig()
        self.assertEqual(list(self.home.rglob("zig")), [])
        self.assertEqual(list(self.home.rglob("zig-install-*")), [])

    def test_legacy_cache_is_reverified_and_receipted_without_changing_payload(self):
        data, _ = self.archive()
        opener, expected = self.configured(data)
        compiler = install_zig()
        before = compiler.stat().st_mtime_ns
        (compiler.parent / CACHE_RECEIPT).unlink()
        self.assertEqual(install_zig(), compiler)
        self.assertEqual(opener.call_count, 2)
        self.assertEqual(compiler.stat().st_mtime_ns, before)
        self.assertTrue(cache_verified(compiler.parent, expected))

    def test_tampered_cache_is_refused_after_rechecking_verified_archive(self):
        data, _ = self.archive()
        opener, _ = self.configured(data)
        compiler = install_zig()
        (compiler.parent / "lib/runtime.h").write_bytes(b"tampered")
        with self.assertRaisesRegex(RuntimeError, "Cached compiler integrity check failed"):
            install_zig()
        self.assertEqual(opener.call_count, 2)
        self.assertEqual((compiler.parent / "lib/runtime.h").read_bytes(), b"tampered")

    def test_valid_archive_without_compiler_is_refused(self):
        data, _ = self.archive(include_compiler=False)
        self.configured(data)
        with self.assertRaisesRegex(RuntimeError, "Compiler not found"):
            install_zig()

    def test_corrupt_archive_and_download_failures_are_environment_errors(self):
        for system, machine in (("Darwin", "arm64"), ("Windows", "AMD64")):
            with self.subTest(system=system):
                self.configured(b"not an archive", system, machine)
                with self.assertRaisesRegex(RuntimeError, "Could not install Zig"):
                    install_zig()
        data, _ = self.archive()
        opener, _ = self.configured(data)
        opener.side_effect = OSError("fixture download unavailable")
        with self.assertRaisesRegex(RuntimeError, "Could not install Zig"):
            install_zig()

    def test_download_size_guard_does_not_require_allocating_a_large_fixture(self):
        class OversizedChunk:
            def __len__(self):
                return 129 * 1024 * 1024
        data, _ = self.archive()
        opener, _ = self.configured(data)
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = OversizedChunk()
        opener.side_effect = None
        opener.return_value = response
        with self.assertRaisesRegex(RuntimeError, "download too large"):
            install_zig()
        response.read.assert_called_once()

    def test_unsupported_platform_never_accesses_network(self):
        with patch("agent.installer.platform.system", return_value="Linux"), patch("agent.installer.platform.machine", return_value="x86_64"), patch("agent.installer.urllib.request.urlopen") as opener, self.assertRaisesRegex(RuntimeError, "Unsupported platform"):
            install_zig()
        opener.assert_not_called()

    def test_tar_traversal_is_rejected_and_cannot_write_outside_stage(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:xz") as archive:
            info = tarfile.TarInfo("../escape")
            info.size = 6
            archive.addfile(info, io.BytesIO(b"escape"))
        self.configured(buffer.getvalue())
        with self.assertRaisesRegex(RuntimeError, "Could not install Zig"):
            install_zig()
        self.assertFalse(any(path.name == "escape" for path in self.home.rglob("*")))

    def test_bad_receipts_fail_closed(self):
        root = self.home / "cache"
        root.mkdir()
        (root / "zig").write_bytes(b"fixture")
        record = root / CACHE_RECEIPT
        self.assertFalse(cache_verified(root, "release"))
        for data in ("{}", "null", "{", "x" * 4097, json.dumps({"version": "old", "release_sha256": "release", "toolchain_sha256": toolchain_digest(root)})):
            with self.subTest(data=data[:20]):
                record.write_text(data)
                self.assertFalse(cache_verified(root, "release"))

    @unittest.skipIf(os.name == "nt", "Symlink fixture requires POSIX privileges")
    def test_internal_links_hash_consistently_but_root_and_receipt_links_are_refused(self):
        root = self.home / "cache"
        root.mkdir()
        (root / "zig").write_bytes(b"fixture")
        (root / "alias").symlink_to("zig")
        self.assertEqual(toolchain_digest(root), toolchain_digest(root))
        linked_root = self.home / "linked"
        linked_root.symlink_to(root, target_is_directory=True)
        with self.assertRaisesRegex(RuntimeError, "root must not be a symlink"):
            toolchain_digest(linked_root)
        (root / CACHE_RECEIPT).symlink_to(root / "zig")
        self.assertFalse(cache_verified(root, "release"))
