import asyncio
import json
from collections import deque
from collections.abc import Callable
from pathlib import Path

from app.config import PROJECT_ROOT, Settings


class AppAudioError(RuntimeError):
    pass


class AppAudioPermissionError(AppAudioError):
    pass


class NativeAppAudioSource:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        log_handle,
        on_close: Callable[["NativeAppAudioSource"], None],
        read_timeout: float,
    ):
        self.process = process
        self._log_handle = log_handle
        self._on_close = on_close
        self._read_timeout = read_timeout
        self._stderr_tail: deque[str] = deque(maxlen=12)
        self._stderr_task = asyncio.create_task(self._read_stderr())
        self._stop_lock = asyncio.Lock()
        self._stopped = False

    @property
    def stderr_tail(self) -> str:
        return " | ".join(self._stderr_tail)

    async def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        while line := await self.process.stderr.readline():
            message = line.decode("utf-8", errors="replace").strip()
            if not message:
                continue
            self._stderr_tail.append(message)
            self._log_handle.write(f"{message}\n")
            self._log_handle.flush()

    async def read(self) -> bytes:
        if self._stopped:
            return b""
        assert self.process.stdout is not None
        try:
            chunk = await asyncio.wait_for(
                self.process.stdout.read(32_768), timeout=self._read_timeout
            )
        except TimeoutError as exc:
            raise AppAudioError(
                f"超过 {self._read_timeout:g} 秒没有收到应用音频数据，请检查目标应用和系统权限"
            ) from exc
        if chunk:
            return chunk
        return_code = await self.process.wait()
        if self._stopped:
            return b""
        detail = self.stderr_tail
        suffix = f"：{detail}" if detail else ""
        raise AppAudioError(f"应用音频捕获已结束（进程代码 {return_code}）{suffix}")

    async def stop(self) -> None:
        async with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            if self.process.returncode is None:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), timeout=3)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            if self._stderr_task is not None:
                await self._stderr_task
            self._log_handle.close()
            self._on_close(self)


class AppAudioCaptureManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._sources: set[NativeAppAudioSource] = set()

    @property
    def helper_path(self) -> Path:
        if self.settings.app_audio_helper_path is not None:
            return self.settings.app_audio_helper_path
        build_dir = PROJECT_ROOT / "native" / "AppAudioHelper" / ".build"
        release = build_dir / "release" / "app-audio-helper"
        if release.exists():
            return release
        return build_dir / "debug" / "app-audio-helper"

    def available(self) -> bool:
        return self.helper_path.is_file()

    def _require_helper(self) -> Path:
        path = self.helper_path
        if not path.is_file():
            raise AppAudioError(
                "按应用音频 helper 尚未构建，请先运行 scripts/build_app_audio_helper.sh"
            )
        return path

    async def _run_json(self, arguments: list[str]):
        helper = self._require_helper()
        process = await asyncio.create_subprocess_exec(
            str(helper),
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15)
        message = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            raise AppAudioError(message or f"原生音频 helper 失败（代码 {process.returncode}）")
        try:
            return json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise AppAudioError("原生音频 helper 返回了无效 JSON") from exc

    async def permissions(self, request: bool = False) -> dict:
        arguments = ["permissions", "--json"]
        if request:
            arguments.append("--request")
        return await self._run_json(arguments)

    async def applications(self) -> list[dict]:
        permission = await self.permissions()
        if not permission.get("screen_recording_granted"):
            raise AppAudioPermissionError(
                "请在系统设置 → 隐私与安全性 → 屏幕与系统音频录制中允许 app-audio-helper"
            )
        return await self._run_json(["list-apps", "--json"])

    async def start(self, session: dict) -> NativeAppAudioSource:
        permission = await self.permissions()
        if not permission.get("screen_recording_granted"):
            raise AppAudioPermissionError(
                "应用音频权限未开启，请在系统设置中允许 app-audio-helper"
            )
        bundle_id = session.get("audio_source_bundle_id")
        process_id = session.get("audio_source_process_id")
        if not bundle_id or not process_id:
            raise AppAudioError("会话缺少目标应用标识，请重新选择应用")
        helper = self._require_helper()
        log_path = self.settings.data_dir / "logs" / "app-audio.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("a", encoding="utf-8")
        try:
            process = await asyncio.create_subprocess_exec(
                str(helper),
                "capture",
                "--bundle-id",
                bundle_id,
                "--pid",
                str(process_id),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
        except Exception:
            log_handle.close()
            raise
        source = NativeAppAudioSource(
            process=process,
            log_handle=log_handle,
            on_close=self._sources.discard,
            read_timeout=self.settings.app_audio_read_timeout,
        )
        self._sources.add(source)
        return source

    async def close(self) -> None:
        sources = list(self._sources)
        if sources:
            await asyncio.gather(*(source.stop() for source in sources))
