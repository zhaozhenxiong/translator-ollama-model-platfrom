import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

from app.config import PROJECT_ROOT, Settings


class ASRProcessManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._process: subprocess.Popen | None = None
        self._monitor_task: asyncio.Task | None = None
        self._finished = asyncio.Event()
        self._status = "stopped"
        self._error: str | None = None
        self._log_handle = None
        self._log_path = settings.data_dir / "logs" / "asr.log"

    @property
    def status(self) -> dict:
        return {
            "status": self._status,
            "backend": self.settings.asr_backend,
            "model": self.settings.asr_model,
            "error": self._error,
        }

    async def start(self) -> None:
        if self._monitor_task is not None:
            return
        self.settings.ensure_directories()
        self._status = "starting"
        self._error = None
        self._finished.clear()
        if self.settings.asr_auto_start:
            executable = Path(sys.executable).with_name("wlk")
            if not executable.exists():
                self._status = "error"
                self._error = f"未找到 WhisperLiveKit 命令：{executable}"
                self._finished.set()
                return
            command = [
                str(executable),
                "--host",
                self.settings.asr_host,
                "--port",
                str(self.settings.asr_port),
                "--backend",
                self.settings.asr_backend,
                "--model",
                self.settings.asr_model,
                "--language",
                self.settings.asr_language,
                "--backend-policy",
                self.settings.asr_backend_policy,
                "--pause-segmentation-seconds",
                str(self.settings.asr_pause_segmentation_seconds),
                "--pcm-input",
            ]
            self._log_handle = self._log_path.open("a", encoding="utf-8")
            environment = os.environ.copy()
            environment.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")
            environment.setdefault("HF_HUB_ETAG_TIMEOUT", "30")
            try:
                self._process = await asyncio.to_thread(
                    subprocess.Popen,
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=self._log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=environment,
                )
            except OSError as exc:
                self._status = "error"
                self._error = str(exc)
                self._finished.set()
                return
        self._monitor_task = asyncio.create_task(self._monitor())

    def _read_log_tail(self) -> str:
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-3000:].strip()
        except OSError:
            return ""

    async def _monitor(self) -> None:
        deadline = time.monotonic() + self.settings.asr_startup_timeout
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                while time.monotonic() < deadline:
                    if self._process is not None and self._process.poll() is not None:
                        detail = self._read_log_tail()
                        raise RuntimeError(
                            f"WhisperLiveKit 已退出（代码 {self._process.returncode}）。{detail}"
                        )
                    try:
                        response = await client.get(f"{self.settings.asr_http_url}/health")
                        if response.status_code == 200:
                            self._status = "ready"
                            self._finished.set()
                            return
                    except httpx.HTTPError:
                        pass
                    await asyncio.sleep(1)
            raise TimeoutError("等待 WhisperLiveKit 就绪超时")
        except asyncio.CancelledError:
            raise
        except (RuntimeError, TimeoutError) as exc:
            self._status = "error"
            self._error = str(exc)
            self._finished.set()

    async def ensure_ready(self) -> None:
        if self._status == "ready":
            return
        if self._status == "error":
            raise RuntimeError(self._error or "WhisperLiveKit 不可用")
        try:
            await asyncio.wait_for(self._finished.wait(), timeout=self.settings.asr_startup_timeout)
        except TimeoutError as exc:
            raise RuntimeError("等待 WhisperLiveKit 就绪超时") from exc
        if self._status != "ready":
            raise RuntimeError(self._error or "WhisperLiveKit 不可用")

    async def stop(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None

        if self._process is not None and self._process.poll() is None:
            try:
                os.killpg(self._process.pid, signal.SIGTERM)
                await asyncio.to_thread(self._process.wait, 10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if self._process.poll() is None:
                    os.killpg(self._process.pid, signal.SIGKILL)
                    await asyncio.to_thread(self._process.wait)
        self._process = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        self._status = "stopped"
