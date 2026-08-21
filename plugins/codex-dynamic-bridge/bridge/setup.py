import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bridge.runtime import find_agy


WINDOWS_INSTALLER_URL = "https://antigravity.google/cli/install.ps1"
UNIX_INSTALLER_URL = "https://antigravity.google/cli/install.sh"
MAX_INSTALLER_BYTES = 1_048_576
UPDATE_BASE_URL = "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app"
MAX_MANIFEST_BYTES = 65_536
MAX_BINARY_BYTES = 268_435_456
DOWNLOAD_CHUNK_BYTES = 65_536
DOWNLOAD_ATTEMPTS = 4
TRUSTED_BINARY_HOSTS = {"storage.googleapis.com"}


class SetupError(RuntimeError):
    """完整能力装载失败。"""


def default_agy_install_dir(env=None):
    env = os.environ if env is None else env
    codex_home = Path(env.get("CODEX_HOME", Path.home() / ".codex"))
    return (codex_home / "tools" / "agy").expanduser().resolve()


def validate_install_dir(path, env=None, allow_system_drive=False, platform_name=None):
    env = os.environ if env is None else env
    platform_name = os.name if platform_name is None else platform_name
    path = Path(path).expanduser().resolve()
    if platform_name == "nt" and not allow_system_drive:
        system_drive = env.get("SystemDrive", "C:").rstrip("\\/").upper()
        if path.drive.upper() == system_drive:
            raise SetupError(
                f"agy 安装目录位于系统盘 {system_drive}；请用 --agy-dir 指定非系统盘，"
                "或在明确授权后传入 --allow-system-drive"
            )
    return path


def read_installer(url, opener=urlopen):
    request = Request(url, headers={"User-Agent": "codex-dynamic-bridge/0.2"})
    try:
        with opener(request, timeout=30) as response:
            content = response.read(MAX_INSTALLER_BYTES + 1)
    except OSError as exc:
        raise SetupError(f"无法下载 Antigravity 官方 agy 安装器: {exc}") from exc
    if not content or len(content) > MAX_INSTALLER_BYTES:
        raise SetupError("Antigravity 官方 agy 安装器为空或超过大小限制")
    return content


def read_resource(url, max_bytes, opener=urlopen, attempts=DOWNLOAD_ATTEMPTS):
    last_error = None
    for _ in range(attempts):
        request = Request(url, headers={"User-Agent": "codex-dynamic-bridge/0.2"})
        try:
            with opener(request, timeout=60) as response:
                content = response.read(max_bytes + 1)
                headers = getattr(response, "headers", {})
                length = headers.get("Content-Length") if headers else None
        except OSError as exc:
            last_error = exc
            continue
        if not content or len(content) > max_bytes:
            last_error = SetupError("Antigravity 官方资源为空或超过大小限制")
            continue
        if length and length.isdigit() and len(content) != int(length):
            last_error = SetupError(
                f"Antigravity 官方资源提前结束: {len(content)}/{length} 字节"
            )
            continue
        return content
    raise SetupError(f"无法完整下载 Antigravity 官方资源: {last_error}")


def windows_manifest_name(machine_name=None):
    machine_name = (machine_name or platform.machine()).strip().lower()
    if machine_name in {"amd64", "x86_64"}:
        return "windows_amd64"
    if machine_name in {"arm64", "aarch64"}:
        return "windows_arm64"
    raise SetupError(f"不支持的 Windows CPU 架构: {machine_name or 'unknown'}")


def read_windows_manifest(machine_name=None, opener=urlopen):
    name = windows_manifest_name(machine_name)
    manifest_url = f"{UPDATE_BASE_URL}/manifests/{name}.json"
    content = read_resource(manifest_url, MAX_MANIFEST_BYTES, opener=opener)
    try:
        manifest = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SetupError("Antigravity agy 发布清单不是有效 UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise SetupError("Antigravity agy 发布清单根节点必须是对象")

    version = manifest.get("version")
    binary_url = manifest.get("url")
    sha512 = manifest.get("sha512")
    parsed = urlparse(binary_url) if isinstance(binary_url, str) else None
    if not isinstance(version, str) or not version.strip():
        raise SetupError("Antigravity agy 发布清单缺少版本")
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname not in TRUSTED_BINARY_HOSTS
    ):
        raise SetupError("Antigravity agy 发布清单包含不可信下载地址")
    if not isinstance(sha512, str) or not re.fullmatch(r"[0-9a-fA-F]{128}", sha512):
        raise SetupError("Antigravity agy 发布清单包含无效 SHA-512")
    return {
        "version": version.strip(),
        "url": binary_url,
        "sha512": sha512.lower(),
        "manifest": manifest_url,
    }


def response_total_bytes(response, offset):
    headers = getattr(response, "headers", {})
    content_range = headers.get("Content-Range") if headers else None
    if content_range:
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range.strip())
        if not match or int(match.group(1)) != offset:
            raise SetupError("agy 下载响应包含无效 Content-Range")
        return int(match.group(3))
    content_length = headers.get("Content-Length") if headers else None
    if content_length and content_length.isdigit():
        return offset + int(content_length)
    return None


def sha512_file(path):
    digest = hashlib.sha512()
    with Path(path).open("rb") as stream:
        while True:
            chunk = stream.read(DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verified_staging_digest(staging_path, expected_sha512):
    if not staging_path.is_file():
        return None
    size = staging_path.stat().st_size
    if not 0 < size <= MAX_BINARY_BYTES:
        return None
    digest = sha512_file(staging_path)
    if digest != expected_sha512:
        return None
    with staging_path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise SetupError("agy 下载文件不是有效的 Windows 可执行文件")
    return digest


def download_verified_binary(
    url,
    expected_sha512,
    staging_path,
    opener=urlopen,
    attempts=DOWNLOAD_ATTEMPTS,
):
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    existing = verified_staging_digest(staging_path, expected_sha512)
    if existing:
        return existing
    last_error = None
    for attempt in range(1, attempts + 1):
        offset = staging_path.stat().st_size if staging_path.is_file() else 0
        if offset > MAX_BINARY_BYTES:
            staging_path.unlink()
            offset = 0
        headers = {"User-Agent": "codex-dynamic-bridge/0.2"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers)
        try:
            with opener(request, timeout=60) as response:
                status = getattr(response, "status", 200)
                if offset and status != 206:
                    offset = 0
                total = response_total_bytes(response, offset)
                mode = "ab" if offset else "wb"
                reader = getattr(response, "read1", response.read)
                with staging_path.open(mode) as stream:
                    while True:
                        chunk = reader(DOWNLOAD_CHUNK_BYTES)
                        if not chunk:
                            break
                        if stream.tell() + len(chunk) > MAX_BINARY_BYTES:
                            raise SetupError("agy 下载文件超过大小限制")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
        except (OSError, SetupError) as exc:
            last_error = exc
            if attempt < attempts:
                continue
            break

        size = staging_path.stat().st_size
        if total is not None and size < total:
            last_error = SetupError(f"agy 下载提前结束: {size}/{total} 字节")
            continue
        if total is not None and size != total:
            staging_path.unlink(missing_ok=True)
            last_error = SetupError(f"agy 下载大小异常: {size}/{total} 字节")
            continue

        digest = sha512_file(staging_path)
        if digest == expected_sha512:
            with staging_path.open("rb") as stream:
                if stream.read(2) != b"MZ":
                    staging_path.unlink(missing_ok=True)
                    raise SetupError("agy 下载文件不是有效的 Windows 可执行文件")
            return digest
        staging_path.unlink(missing_ok=True)
        last_error = SetupError(
            f"agy 下载文件 SHA-512 校验失败（第 {attempt}/{attempts} 次）"
        )

    raise SetupError(f"agy 下载失败，未修改安装目录: {last_error}")


def download_verified_binary_with_curl(
    url,
    expected_sha512,
    staging_path,
    env=None,
    runner=subprocess.run,
    fallback_opener=urlopen,
    prefer_curl=True,
):
    staging_path.parent.mkdir(parents=True, exist_ok=True)
    existing = verified_staging_digest(staging_path, expected_sha512)
    if existing:
        return existing

    env = os.environ if env is None else env
    curl = shutil.which("curl.exe", path=env.get("PATH")) if prefer_curl else None
    if curl:
        command = [
            curl,
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--retry",
            "8",
            "--retry-all-errors",
            "--continue-at",
            "-",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--output",
            str(staging_path),
            url,
        ]
        try:
            result = runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=7200,
                check=False,
                env=dict(env),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = None
            curl_error = str(exc)
        else:
            curl_error = (result.stderr or result.stdout or "curl 下载失败").strip()
        if result is not None and result.returncode == 0:
            digest = verified_staging_digest(staging_path, expected_sha512)
            if digest:
                return digest
            staging_path.unlink(missing_ok=True)
            curl_error = "curl 下载完成但 SHA-512 校验失败"
        if staging_path.is_file() and staging_path.stat().st_size > MAX_BINARY_BYTES:
            staging_path.unlink()
        try:
            return download_verified_binary(
                url,
                expected_sha512,
                staging_path,
                opener=fallback_opener,
            )
        except SetupError as exc:
            raise SetupError(f"curl 加速失败（{curl_error}）；标准库回退也失败: {exc}") from exc

    return download_verified_binary(
        url,
        expected_sha512,
        staging_path,
        opener=fallback_opener,
    )


def install_windows_agy(
    install_dir,
    machine_name=None,
    opener=urlopen,
    env=None,
    runner=subprocess.run,
    prefer_curl=True,
):
    manifest = read_windows_manifest(machine_name=machine_name, opener=opener)
    install_dir.mkdir(parents=True, exist_ok=True)
    safe_version = re.sub(r"[^A-Za-z0-9._-]", "_", manifest["version"])
    staging_path = (
        install_dir.parent
        / "cache"
        / "agy-staging"
        / f"agy-{safe_version}.partial"
    )
    digest = download_verified_binary_with_curl(
        manifest["url"],
        manifest["sha512"],
        staging_path,
        env=env,
        runner=runner,
        fallback_opener=opener,
        prefer_curl=prefer_curl,
    )
    executable = install_dir / "agy.exe"
    os.replace(staging_path, executable)
    return {
        "available": True,
        "installed": True,
        "path": str(executable.resolve()),
        "version": manifest["version"],
        "manifest": manifest["manifest"],
        "sha512": digest,
    }


def ensure_agy(
    install_dir=None,
    env=None,
    allow_system_drive=False,
    platform_name=None,
    machine_name=None,
    prefer_curl=True,
    opener=urlopen,
    runner=subprocess.run,
):
    env = dict(os.environ if env is None else env)
    existing = find_agy(env)
    if existing:
        return {"available": True, "installed": False, "path": existing}

    platform_name = os.name if platform_name is None else platform_name
    install_dir = validate_install_dir(
        install_dir or default_agy_install_dir(env),
        env=env,
        allow_system_drive=allow_system_drive,
        platform_name=platform_name,
    )
    if platform_name == "nt":
        return install_windows_agy(
            install_dir,
            machine_name=machine_name,
            opener=opener,
            env=env,
            runner=runner,
            prefer_curl=prefer_curl,
        )

    install_dir.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".sh"
    installer_url = UNIX_INSTALLER_URL
    installer = install_dir.parent / f".agy-installer-{uuid.uuid4().hex}{suffix}"
    installer.write_bytes(read_installer(installer_url, opener=opener))
    executable = install_dir / ("agy.exe" if platform_name == "nt" else "agy")

    child_env = dict(env)
    shell = shutil.which("sh", path=env.get("PATH"))
    if not shell:
        installer.unlink(missing_ok=True)
        raise SetupError("未找到 sh，无法运行 Antigravity 官方 agy 安装器")
    command = [shell, str(installer), "--dir", str(install_dir)]

    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            env=child_env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SetupError(f"无法运行 Antigravity 官方 agy 安装器: {exc}") from exc
    finally:
        installer.unlink(missing_ok=True)

    if result.returncode != 0 or not executable.is_file():
        detail = (result.stderr or result.stdout or "安装器未生成 agy").strip()
        raise SetupError(f"agy 安装失败: {detail[:1000]}")
    return {
        "available": True,
        "installed": True,
        "path": str(executable.resolve()),
        "installer": installer_url,
    }
