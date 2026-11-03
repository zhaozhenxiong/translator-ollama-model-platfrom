import asyncio
import contextlib
import json
import math
import sys
from array import array
from dataclasses import dataclass
from urllib.parse import urlencode

import websockets
from fastapi import WebSocket, WebSocketDisconnect

from app.asr import ASRProcessManager
from app.audio_capture import AppAudioCaptureManager, AppAudioError, NativeAppAudioSource
from app.config import Settings
from app.exporters import write_markdown_mirror
from app.storage import Storage
from app.translator import TranslationService


@dataclass
class TranslationJob:
    segment: dict
    context: list[str]


def parse_timestamp_ms(value: str | float | None) -> int:
    if value is None:
        return 0
    if isinstance(value, int | float):
        return max(0, round(value * 1000))
    parts = value.strip().split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = parts
            return max(
                0,
                round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000),
            )
        return max(0, round(float(value) * 1000))
    except ValueError:
        return 0


def language_base(language: str | None) -> str:
    return (language or "").lower().replace("_", "-").split("-")[0]


def pcm_rms_level(chunk: bytes) -> float:
    usable_length = len(chunk) - len(chunk) % 2
    if usable_length == 0:
        return 0.0
    samples = array("h")
    samples.frombytes(chunk[:usable_length])
    if sys.byteorder != "little":
        samples.byteswap()
    rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples))
    return min(1.0, rms / 32768.0)


class LiveSession:
    def __init__(
        self,
        websocket: WebSocket,
        session: dict,
        settings: Settings,
        storage: Storage,
        translator: TranslationService,
        asr_manager: ASRProcessManager,
        capture_manager: AppAudioCaptureManager | None = None,
    ):
        self.websocket = websocket
        self.session = session
        self.settings = settings
        self.storage = storage
        self.translator = translator
        self.asr_manager = asr_manager
        self.capture_manager = capture_manager
        self._capture_source: NativeAppAudioSource | None = None
        self._capture_control_task: asyncio.Task | None = None
        self._capture_paused = False
        self.translation_queue: asyncio.Queue[TranslationJob] = asyncio.Queue(
            maxsize=settings.translation_queue_size
        )
        self._seen_lines: set[tuple] = set()
        self._pending_line: dict | None = None
        self._pending_message: dict | None = None
        self._source_history: list[str] = []
        self._asr_ws = None
        self._reader_task: asyncio.Task | None = None
        self._worker_task: asyncio.Task | None = None
        self._send_lock = asyncio.Lock()

    async def _send(self, payload: dict) -> None:
        async with self._send_lock:
            await self.websocket.send_json(payload)

    async def _status(self, status: str, message: str | None = None) -> None:
        payload = {
            "type": "status",
            "status": status,
            "message": message,
            "translation_queue": self.translation_queue.qsize(),
        }
        await self._send(payload)

    async def _mirror(self) -> None:
        detail = self.storage.get_session_detail(self.session["id"])
        if detail is not None:
            segments = detail.pop("segments")
            await asyncio.to_thread(write_markdown_mirror, self.settings.data_dir, detail, segments)

    async def run(self) -> None:
        await self.websocket.accept()
        completed = False
        try:
            self.storage.set_session_status(self.session["id"], "active")
            await self._status("asr_starting", "正在准备本地听写模型")
            await self.asr_manager.ensure_ready()
            query = urlencode({"language": self.session["source_language"], "mode": "full"})
            async with websockets.connect(
                f"{self.settings.asr_ws_url}?{query}",
                ping_interval=20,
                ping_timeout=30,
                open_timeout=30,
                max_size=16 * 1024 * 1024,
            ) as asr_ws:
                self._asr_ws = asr_ws
                self._reader_task = asyncio.create_task(self._read_asr())
                self._worker_task = asyncio.create_task(self._translation_worker())
                if self.session.get("audio_source_type") == "application":
                    if self.capture_manager is None:
                        raise RuntimeError("应用音频捕获服务不可用")
                    await self._send(
                        {
                            "type": "capture_starting",
                            "display_name": self.session.get("audio_source_display_name"),
                        }
                    )
                    self._capture_source = await self.capture_manager.start(self.session)
                    await self._status("ready", "正在等待应用音频")
                    await self._receive_native_audio()
                else:
                    await self._status("ready", "可以开始说话")
                    await self._receive_audio()
                await self._reader_task
                await self._status("finishing", "正在完成剩余翻译")
                await self.translation_queue.join()
                self.storage.set_session_status(self.session["id"], "completed", ended=True)
                await self._mirror()
                await self._send({"type": "session_completed", "session_id": self.session["id"]})
                completed = True
                await self.websocket.close(code=1000)
        except WebSocketDisconnect:
            self.storage.set_session_status(self.session["id"], "interrupted")
        except Exception as exc:  # noqa: BLE001
            self.storage.set_session_status(self.session["id"], "error", ended=True)
            with contextlib.suppress(Exception):
                await self._send({"type": "error", "message": str(exc)})
            with contextlib.suppress(Exception):
                await self.websocket.close(code=1011)
        finally:
            if self._capture_source is not None:
                with contextlib.suppress(Exception):
                    await self._capture_source.stop()
            tasks = [self._reader_task, self._worker_task, self._capture_control_task]
            for task in tasks:
                if task is not None and not task.done():
                    task.cancel()
            for task in tasks:
                if task is not None:
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await task
            if not completed:
                self.storage.fail_unfinished_translations(
                    self.session["id"], "会话中断，可以稍后重试翻译"
                )
                with contextlib.suppress(Exception):
                    await self._mirror()

    async def _receive_audio(self) -> None:
        while True:
            receive_task = asyncio.create_task(self.websocket.receive())
            done, _ = await asyncio.wait(
                {receive_task, self._reader_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if self._reader_task in done:
                receive_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await receive_task
                await self._reader_task
                raise RuntimeError("听写连接在停止前意外结束")

            message = receive_task.result()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if message.get("bytes") is not None:
                await self._asr_ws.send(message["bytes"])
                continue
            text = message.get("text")
            if not text:
                continue
            command = json.loads(text)
            if command.get("type") == "stop":
                await self._asr_ws.send(b"")
                await self._status("draining", "正在完成最后一段听写")
                return

    async def _receive_native_audio(self) -> None:
        source = self._capture_source
        if source is None:
            raise RuntimeError("应用音频输入尚未启动")
        self._capture_control_task = asyncio.create_task(self._receive_native_controls())
        read_task: asyncio.Task | None = None
        received_audio = False
        last_level_at = 0.0
        try:
            while True:
                read_task = asyncio.create_task(source.read())
                done, _ = await asyncio.wait(
                    {read_task, self._capture_control_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if self._capture_control_task in done:
                    read_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await read_task
                    await self._capture_control_task
                    await source.stop()
                    await self._asr_ws.send(b"")
                    await self._send({"type": "capture_ended", "reason": "stopped"})
                    await self._status("draining", "正在完成最后一段听写")
                    return
                try:
                    chunk = read_task.result()
                except AppAudioError as exc:
                    await source.stop()
                    if not received_audio:
                        raise
                    await self._send({"type": "capture_error", "message": str(exc)})
                    await self._send({"type": "capture_ended", "reason": "error"})
                    await self._asr_ws.send(b"")
                    await self._status("draining", "应用音频已结束，正在保存最后内容")
                    return

                if not received_audio:
                    received_audio = True
                    await self._send(
                        {
                            "type": "capture_ready",
                            "display_name": self.session.get("audio_source_display_name"),
                            "sample_rate": 16_000,
                            "channels": 1,
                        }
                    )
                now = asyncio.get_running_loop().time()
                if now - last_level_at >= 0.25:
                    last_level_at = now
                    await self._send(
                        {
                            "type": "capture_level",
                            "level": 0.0 if self._capture_paused else pcm_rms_level(chunk),
                            "paused": self._capture_paused,
                        }
                    )
                await self._asr_ws.send(b"\x00" * len(chunk) if self._capture_paused else chunk)
        finally:
            if read_task is not None and not read_task.done():
                read_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await read_task

    async def _receive_native_controls(self) -> None:
        while True:
            message = await self.websocket.receive()
            if message["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            text = message.get("text")
            if not text:
                continue
            command = json.loads(text)
            command_type = command.get("type")
            if command_type == "stop":
                return
            if command_type == "pause":
                self._capture_paused = True
                await self._status("paused", "应用音频已暂停，暂停期间不会记录声音")
            elif command_type == "resume":
                self._capture_paused = False
                await self._status("ready", "正在捕获应用音频")

    async def _read_asr(self) -> None:
        async for raw_message in self._asr_ws:
            if isinstance(raw_message, bytes):
                continue
            message = json.loads(raw_message)
            if message.get("type") == "config":
                continue
            if message.get("type") == "ready_to_stop":
                await self._flush_pending_line()
                return
            if message.get("error") or message.get("status") == "error":
                raise RuntimeError(message.get("error") or "WhisperLiveKit 听写失败")
            if "lines" in message:
                await self._process_line_snapshot(message.get("lines") or [], message)
            elif "buffer_transcription" in message:
                await self._send_partial(message)
        await self._flush_pending_line()

    async def _process_line_snapshot(self, lines: list[dict], message: dict) -> None:
        # Full mode repeats the entire transcript. Only its last speech line can still grow.
        speech_lines = [
            line
            for line in lines
            if (line.get("text") or "").strip() and line.get("speaker") != -2
        ]
        complete_lines = speech_lines
        pending_line = None
        if speech_lines and lines and lines[-1] is speech_lines[-1]:
            complete_lines = speech_lines[:-1]
            pending_line = speech_lines[-1]
        for line in complete_lines:
            await self._commit_line(line, message)
        self._pending_line = pending_line
        self._pending_message = message if pending_line is not None else None
        await self._send_partial(message)

    async def _send_partial(self, message: dict) -> None:
        pending_text = ((self._pending_line or {}).get("text") or "").strip()
        buffer_text = (message.get("buffer_transcription") or "").strip()
        await self._send(
            {
                "type": "partial",
                "text": " ".join(part for part in (pending_text, buffer_text) if part),
                "asr_lag": message.get("remaining_time_transcription", 0),
            }
        )

    async def _flush_pending_line(self) -> None:
        line = self._pending_line
        message = self._pending_message or {}
        self._pending_line = None
        self._pending_message = None
        if line is not None:
            await self._commit_line(line, message)

    async def _commit_line(self, line: dict, message: dict) -> None:
        text = (line.get("text") or "").strip()
        if not text or line.get("speaker") == -2:
            return
        key = (line.get("speaker"), line.get("start"), line.get("end"), text)
        if key in self._seen_lines:
            return
        self._seen_lines.add(key)
        detected_language = line.get("detected_language") or message.get("detected_language")
        segment = self.storage.add_segment(
            session_id=self.session["id"],
            start_ms=parse_timestamp_ms(line.get("start")),
            end_ms=parse_timestamp_ms(line.get("end")),
            source_text=text,
            detected_language=detected_language,
        )
        context = self._source_history[-2:]
        self._source_history.append(text)
        await self._mirror()
        await self._send({"type": "segment", "segment": segment})
        await self.translation_queue.put(TranslationJob(segment=segment, context=context))
        await self._status("transcribing")

    async def _translation_worker(self) -> None:
        while True:
            job = await self.translation_queue.get()
            try:
                await self._translate_job(job)
            finally:
                self.translation_queue.task_done()
                with contextlib.suppress(Exception):
                    await self._status("transcribing")

    async def _translate_job(self, job: TranslationJob) -> None:
        segment = job.segment
        segment_id = segment["id"]
        source_language = segment.get("detected_language") or self.session["source_language"]
        if language_base(source_language) and language_base(source_language) == language_base(
            self.session["target_language"]
        ):
            updated = self.storage.update_translation(segment_id, segment["source_text"])
            await self._mirror()
            await self._send({"type": "translation_final", "segment": updated})
            return

        attempts = max(1, self.settings.translation_max_attempts)
        for attempt in range(attempts):
            self.storage.set_translation_status(segment_id, "translating")
            await self._send(
                {
                    "type": "translation_update",
                    "segment_id": segment_id,
                    "text": "",
                    "status": "translating",
                }
            )

            async def on_update(text: str) -> None:
                await self._send(
                    {
                        "type": "translation_update",
                        "segment_id": segment_id,
                        "text": text,
                        "status": "translating",
                    }
                )

            try:
                translated = await self.translator.translate(
                    provider=self.session.get("translation_provider", "ollama"),
                    model=self.session.get("translation_model") or self.session["ollama_model"],
                    text=segment["source_text"],
                    source_language=source_language,
                    target_language=self.session["target_language"],
                    context=job.context,
                    on_update=on_update,
                )
                updated = self.storage.update_translation(segment_id, translated)
                await self._mirror()
                await self._send({"type": "translation_final", "segment": updated})
                return
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                message = str(exc)
                self.storage.set_translation_status(segment_id, "failed", message)
                await self._mirror()
                await self._send(
                    {
                        "type": "translation_update",
                        "segment_id": segment_id,
                        "text": "",
                        "status": "failed",
                    }
                )
                await self._send(
                    {
                        "type": "translation_error",
                        "segment_id": segment_id,
                        "message": message,
                    }
                )
