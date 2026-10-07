import hashlib
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bridge import setup


class Response(io.BytesIO):
    def __init__(self, content):
        super().__init__(content)
        self.headers = {"Content-Length": str(len(content))}
        self.status = 200


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agy-setup-test-", dir=Path(__file__).resolve().parents[1]
        )
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "managed"
        self.executable = self.directory / "agy.exe"
        self.payload = b"MZ-official-latest"
        self.opened = []
        self.version = "1.3.1"

    def install_existing(self, content=b"MZ-old"):
        self.directory.mkdir(exist_ok=True)
        self.executable.write_bytes(content)

    def opener(self, request, **_kwargs):
        self.opened.append(request.full_url)
        if "/manifests/" in request.full_url:
            content = json.dumps({
                "version": self.version,
                "url": "https://storage.googleapis.com/example/agy.exe",
                "sha512": hashlib.sha512(self.payload).hexdigest(),
            }).encode("utf-8")
        else:
            content = self.payload
        return Response(content)

    @staticmethod
    def runner(*_args, **_kwargs):
        return SimpleNamespace(stdout="agy 1.2.0\n", stderr="", returncode=0)

    def ensure(self, **kwargs):
        return setup.ensure_agy(
            install_dir=self.directory, env={"PATH": "", "SystemDrive": "C:"},
            platform_name="nt", machine_name="AMD64", prefer_curl=False,
            opener=self.opener, runner=self.runner, update=True, **kwargs,
        )

    def test_相同清单hash无需下载或执行二进制(self):
        self.install_existing(self.payload)
        with mock.patch.object(setup, "agy_version", side_effect=AssertionError("无需执行")):
            result = self.ensure()
        self.assertFalse(result["installed"])
        self.assertFalse(result["updated"])
        self.assertEqual(result["installedVersion"], self.version)
        self.assertEqual(len(self.opened), 1)

    def test_升级和同版本损坏均按hash修复(self):
        for reported_version in ("1.2.0", "1.3.1"):
            with self.subTest(version=reported_version):
                self.install_existing(b"MZ-corrupted")
                with mock.patch.object(setup, "agy_version", return_value=reported_version):
                    result = self.ensure()
                self.assertTrue(result["updated"])
                self.assertEqual(self.executable.read_bytes(), self.payload)

    def test_更高的有效版本不降级(self):
        self.install_existing(b"MZ-newer")
        with mock.patch.object(setup, "agy_version", return_value="1.4.0"):
            result = self.ensure()
        self.assertFalse(result["updated"])
        self.assertEqual(result["installedVersion"], "1.4.0")
        self.assertEqual(self.executable.read_bytes(), b"MZ-newer")
        self.assertEqual(len(self.opened), 1)

    def test_默认不覆盖外部PATH程序且指定目录优先(self):
        external = self.root / "external.exe"
        external.write_bytes(b"external")
        env = {"CODEX_HOME": str(self.root / "codex"), "PATH": ""}
        with mock.patch.object(setup, "find_agy", return_value=str(external)):
            result = setup.ensure_agy(env=env, platform_name="nt", update=True)
            self.assertFalse(result["managed"])
            self.assertFalse((self.root / "codex").exists())
            result = self.ensure()
        self.assertTrue(result["installed"])
        self.assertTrue(result["managed"])
        self.assertEqual(external.read_bytes(), b"external")

    def test_忙替换保留旧文件和验证缓存并可无下载重试(self):
        self.install_existing()
        replace = setup.os.replace

        def busy_replace(source, destination):
            if Path(destination) == self.executable:
                raise PermissionError("正在使用")
            return replace(source, destination)

        with mock.patch.object(setup.os, "replace", side_effect=busy_replace):
            pending = self.ensure()
        self.assertTrue(pending["pending"])
        self.assertFalse(pending["installed"])
        self.assertFalse(pending["updated"])
        self.assertEqual(self.executable.read_bytes(), b"MZ-old")
        self.assertEqual(Path(pending["stagingPath"]).read_bytes(), self.payload)
        self.opened.clear()
        updated = self.ensure()
        self.assertTrue(updated["updated"])
        self.assertEqual(len(self.opened), 1)
        self.assertEqual(self.executable.read_bytes(), self.payload)
        self.assertFalse(Path(pending["stagingPath"]).exists())

    def test_下载中断保留旧文件并可重试(self):
        self.install_existing()
        opener = self.opener

        def interrupted(request, **kwargs):
            if "/manifests/" not in request.full_url:
                raise KeyboardInterrupt()
            return opener(request, **kwargs)

        with mock.patch.object(self, "opener", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.ensure()
        self.assertEqual(self.executable.read_bytes(), b"MZ-old")
        self.assertFalse(list(self.root.glob("cache/agy-staging/*.partial")))
        self.assertTrue(self.ensure()["updated"])

    def test_缓存原子发布也被占用时保留独立暂存并可复用(self):
        self.install_existing()
        with mock.patch.object(setup.os, "replace", side_effect=PermissionError("占用")):
            pending = self.ensure()
        self.assertTrue(pending["pending"])
        staging = Path(pending["stagingPath"])
        self.assertTrue(staging.name.endswith(".partial"))
        self.assertEqual(staging.read_bytes(), self.payload)
        self.opened.clear()
        self.assertTrue(self.ensure()["updated"])
        self.assertEqual(len(self.opened), 1)
        self.assertFalse(staging.exists())

    def test_已有托管文件时默认仍报告活动外部程序(self):
        codex_home = self.root / "codex"
        managed_dir = codex_home / "tools" / "agy"
        managed_dir.mkdir(parents=True)
        managed = managed_dir / "agy.exe"
        managed.write_bytes(self.payload)
        external = self.root / "external.exe"
        external.write_bytes(b"MZ-external")
        env = {"CODEX_HOME": str(codex_home), "CODEX_DYNAMIC_BRIDGE_AGY": str(external)}
        status = setup.agy_install_status(env=env, platform_name="nt", runner=self.runner)
        result = setup.ensure_agy(env=env, platform_name="nt", update=True)
        self.assertEqual(status["path"], str(external))
        self.assertFalse(status["managed"])
        self.assertEqual(result["path"], str(external))
        self.assertEqual(result["updateSkipped"], "external_managed")
        explicit = setup.agy_install_status(
            install_dir=managed_dir, env=env, platform_name="nt", runner=self.runner,
        )
        self.assertEqual(explicit["path"], str(managed))
        self.assertTrue(explicit["managed"])
        with mock.patch.object(setup, "find_agy", return_value=str(external)):
            env.pop("CODEX_DYNAMIC_BRIDGE_AGY")
            active_path = setup.ensure_agy(env=env, platform_name="nt", update=True)
        self.assertEqual(active_path["path"], str(external))
        self.assertFalse(active_path["managed"])
        self.assertEqual(managed.read_bytes(), self.payload)

    def test_消费完整独立缓存后noop也清理旧来源(self):
        self.install_existing()
        with mock.patch.object(setup.os, "replace", side_effect=PermissionError("占用")):
            pending = self.ensure()
        source = Path(pending["stagingPath"])
        download = setup.download_verified_binary_with_curl

        def already_installed(*args, **kwargs):
            digest = download(*args, **kwargs)
            self.executable.write_bytes(self.payload)
            return digest

        with mock.patch.object(setup, "download_verified_binary_with_curl", side_effect=already_installed):
            result = self.ensure()
        self.assertFalse(result["installed"])
        self.assertFalse(source.exists())

    def test_清理消费来源前重新验证并保留正在变化的数据(self):
        self.install_existing()
        with mock.patch.object(setup.os, "replace", side_effect=PermissionError("占用")):
            pending = self.ensure()
        source = Path(pending["stagingPath"])
        download = setup.download_verified_binary_with_curl

        def source_changed(*args, **kwargs):
            digest = download(*args, **kwargs)
            source.write_bytes(b"still-downloading")
            return digest

        with mock.patch.object(setup, "download_verified_binary_with_curl", side_effect=source_changed):
            result = self.ensure()
        self.assertTrue(result["updated"])
        self.assertEqual(source.read_bytes(), b"still-downloading")

    def test_下载期间出现更高版本不覆盖(self):
        self.install_existing()
        opener = self.opener

        def newer_writer(request, **kwargs):
            if "/manifests/" not in request.full_url:
                self.executable.write_bytes(b"MZ-newer")
            return opener(request, **kwargs)

        def version(executable, runner):
            return "1.4.0" if Path(executable).read_bytes() == b"MZ-newer" else "1.2.0"

        with (
            mock.patch.object(self, "opener", side_effect=newer_writer),
            mock.patch.object(setup, "agy_version", side_effect=version),
        ):
            result = self.ensure()
        self.assertFalse(result["updated"])
        self.assertEqual(self.executable.read_bytes(), b"MZ-newer")

    def test_status默认不联网且联网失败仍可读取本地信息(self):
        self.install_existing()
        self.assertEqual(setup.agy_version(self.executable, self.runner), "1.2.0")
        kwargs = dict(
            install_dir=self.directory, env={"CODEX_HOME": str(self.root / "codex")},
            platform_name="nt", runner=self.runner,
        )
        with mock.patch.object(setup, "read_windows_manifest", side_effect=setup.SetupError("离线")) as manifest:
            local = setup.agy_install_status(**kwargs)
            manifest.assert_not_called()
            online = setup.agy_install_status(check_update=True, **kwargs)
        self.assertTrue(local["managed"])
        self.assertEqual(local["installedVersion"], "1.2.0")
        self.assertEqual(online["updateCheckError"], "离线")
        self.assertFalse((self.root / "codex").exists())

    def test_并发下载使用独立暂存并且只替换一次(self):
        self.install_existing()
        barrier = threading.Barrier(2)
        opener = self.opener

        def concurrent_opener(request, **kwargs):
            if "/manifests/" not in request.full_url:
                barrier.wait(timeout=5)
            return opener(request, **kwargs)

        with mock.patch.object(self, "opener", side_effect=concurrent_opener):
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _item: self.ensure(), range(2)))
        self.assertEqual(sum(result["updated"] for result in results), 1)
        self.assertEqual(self.executable.read_bytes(), self.payload)
        self.assertFalse(list(self.root.glob("cache/agy-staging/*.partial")))

    def test_status联网比较完整性及版本并且不写文件(self):
        self.install_existing()
        kwargs = dict(
            install_dir=self.directory, platform_name="nt", machine_name="AMD64",
            opener=self.opener, runner=self.runner, check_update=True,
        )
        old = setup.agy_install_status(**kwargs)
        self.assertTrue(old["updateAvailable"])
        self.assertFalse(old["integrityMatchesManifest"])
        self.assertEqual(old["latestVersion"], self.version)
        self.install_existing(self.payload)
        same = setup.agy_install_status(**kwargs)
        self.assertFalse(same["updateAvailable"])
        self.assertTrue(same["integrityMatchesManifest"])
        self.assertFalse((self.root / "cache").exists())

    def test_版本排序符合稳定版和预发布数字规则(self):
        self.assertGreater(setup.version_key("1.3.1"), setup.version_key("1.3.1-rc.10"))
        self.assertGreater(setup.version_key("1.3.1-rc.10"), setup.version_key("1.3.1-rc.2"))
        self.assertEqual(setup.version_key("1.3.1+build.1"), setup.version_key("1.3.1+build.2"))

    def test_clean仅清理自身缓存文件且可重复执行和重试占用(self):
        self.install_existing()
        cache = self.root / "cache" / "agy-staging"
        cache.mkdir(parents=True)
        partial = cache / "agy-1.2.3.partial"
        verified = cache / "agy-official.verified"
        user_file = cache / "user-data.json"
        lock = cache / "agy-official.verified.lock"
        for path in (partial, verified, user_file, lock):
            path.write_bytes(b"MZ-cache")
        nested = cache / "agy-nested.partial"
        nested.mkdir()
        (nested / "user-data.txt").write_text("保留", encoding="utf-8")
        unlink = Path.unlink

        def occupied(path, *args, **kwargs):
            if path == verified:
                raise PermissionError("占用")
            return unlink(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", occupied):
            first = setup.clean_agy_cache(self.directory, platform_name="nt")
        self.assertEqual(first["removed"], [str(partial)])
        self.assertTrue(verified.exists())
        self.assertTrue(any(item["path"] == str(verified) for item in first["skipped"]))
        retried = setup.clean_agy_cache(self.directory, platform_name="nt")
        self.assertEqual(retried["removed"], [str(verified)])
        self.assertFalse(setup.clean_agy_cache(self.directory, platform_name="nt")["removed"])
        self.assertEqual(self.executable.read_bytes(), b"MZ-old")
        self.assertTrue(user_file.exists())
        self.assertTrue(lock.exists())
        self.assertEqual((nested / "user-data.txt").read_text(encoding="utf-8"), "保留")

    def test_clean跳过符号链接文件和目录(self):
        cache = self.root / "cache" / "agy-staging"
        cache.mkdir(parents=True)
        outside = self.root / "outside.partial"
        outside.write_bytes(b"user-data")
        link = cache / "agy-link.partial"
        real_link = True
        try:
            link.symlink_to(outside)
        except OSError:
            # Windows 未启用符号链接权限时仍验证链接状态分支。
            real_link = False
            link.write_bytes(b"link-fixture")
        is_symlink = Path.is_symlink
        with mock.patch.object(Path, "is_symlink", lambda path: path == link or is_symlink(path)):
            result = setup.clean_agy_cache(self.directory, platform_name="nt")
        self.assertFalse(result["removed"])
        self.assertTrue(result["skipped"])
        self.assertEqual(outside.read_bytes(), b"user-data")
        link.unlink()
        cache.rmdir()
        outside_dir = self.root / "outside-cache"
        outside_dir.mkdir()
        protected = outside_dir / "agy-official.partial"
        protected.write_bytes(b"user-data")
        if real_link:
            cache.symlink_to(outside_dir, target_is_directory=True)
        else:
            cache.mkdir()
        with mock.patch.object(Path, "is_symlink", lambda path: path == cache or is_symlink(path)):
            result = setup.clean_agy_cache(self.directory, platform_name="nt")
        self.assertFalse(result["removed"])
        self.assertEqual(result["skipped"][0]["reason"], "symbolic_link")
        self.assertEqual(protected.read_bytes(), b"user-data")


if __name__ == "__main__":
    unittest.main()
