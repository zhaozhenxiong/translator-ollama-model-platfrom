import asyncio
import json
import time

from fastapi.testclient import TestClient

from app.asr import ASRProcessManager
from app.audio_capture import AppAudioCaptureManager, AppAudioError
from app.main import create_app
from app.translator import OllamaTranslator, TranslationService


class FakeASRConnection:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.audio = bytearray()
        self.received_audio = False

    async def __aenter__(self):
        await self.messages.put({"type": "config"})
        return self

    async def __aexit__(self, *_):
        return None

    async def send(self, data):
        if data:
            self.audio.extend(data)
            if not self.received_audio:
                self.received_audio = True
                await self.messages.put(
                    {
                        "status": "active_transcription",
                        "buffer_transcription": "Hel",
                        "lines": [],
                    }
                )
                await self.messages.put(
                    {
                        "status": "active_transcription",
                        "buffer_transcription": "",
                        "lines": [
                            {
                                "speaker": 1,
                                "start": "0:00:00",
                                "end": "0:00:01",
                                "text": "Hello",
                                "detected_language": "en",
                            }
                        ],
                    }
                )
            return
        await self.messages.put({"type": "ready_to_stop"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        message = await self.messages.get()
        if message.get("type") == "ready_to_stop":
            raise StopAsyncIteration
        return json.dumps(message)


async def no_wait(_):
    return None


async def fake_translate(self, **kwargs):
    await kwargs["on_update"]("你")
    await kwargs["on_update"]("你好")
    return "你好"


def test_live_websocket_pipeline(settings, monkeypatch) -> None:
    fake_asr = FakeASRConnection()
    monkeypatch.setattr(ASRProcessManager, "ensure_ready", no_wait)
    monkeypatch.setattr("app.live.websockets.connect", lambda *args, **kwargs: fake_asr)
    monkeypatch.setattr(OllamaTranslator, "translate", fake_translate)
    app = create_app(settings)

    with TestClient(app) as client:
        session = client.post(
            "/api/sessions", json={"source_language": "en", "target_language": "zh"}
        ).json()
        events = []
        with client.websocket_connect(f"/ws/live?session_id={session['id']}") as websocket:
            websocket.send_bytes(b"\x00\x00" * 160)
            websocket.send_json({"type": "stop"})
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "session_completed":
                    break

        event_types = [event["type"] for event in events]
        assert "partial" in event_types
        assert "segment" in event_types
        assert "translation_update" in event_types
        assert "translation_final" in event_types
        assert event_types[-1] == "session_completed"
        assert fake_asr.audio == b"\x00\x00" * 160

        detail = app.state.storage.get_session_detail(session["id"])
        assert detail["status"] == "completed"
        assert len(detail["segments"]) == 1
        assert detail["segments"][0]["source_text"] == "Hello"
        assert detail["segments"][0]["translated_text"] == "你好"


def test_cc_switch_live_uses_session_snapshot(settings, monkeypatch) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})
    fake_asr = FakeASRConnection()
    calls = []
    monkeypatch.setattr(ASRProcessManager, "ensure_ready", no_wait)
    monkeypatch.setattr("app.live.websockets.connect", lambda *args, **kwargs: fake_asr)

    async def route_translate(self, **kwargs):
        calls.append(kwargs)
        await kwargs["on_update"]("你好")
        return "你好"

    monkeypatch.setattr(TranslationService, "translate", route_translate)
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post(
            "/api/sessions",
            json={"translation_provider": "cc_switch", "translation_model": "gpt-5.5"},
        ).json()
        with client.websocket_connect(f"/ws/live?session_id={session['id']}") as websocket:
            websocket.send_bytes(b"\x00\x00" * 160)
            websocket.send_json({"type": "stop"})
            receive_until(websocket, "session_completed")

        assert calls[0]["provider"] == "cc_switch"
        assert calls[0]["model"] == "gpt-5.5"
        assert app.state.storage.get_session(session["id"])["status"] == "completed"


class FakeNativeSource:
    def __init__(self):
        self.chunks = asyncio.Queue()
        self.stopped = False

    async def read(self):
        item = await self.chunks.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def stop(self):
        self.stopped = True


def receive_until(websocket, event_type: str) -> dict:
    for _ in range(100):
        event = websocket.receive_json()
        if event["type"] == event_type:
            return event
    raise AssertionError(f"Did not receive {event_type}")


def receive_until_status(websocket, status: str) -> dict:
    for _ in range(100):
        event = websocket.receive_json()
        if event["type"] == "status" and event.get("status") == status:
            return event
    raise AssertionError(f"Did not receive status {status}")


def application_session(client):
    return client.post(
        "/api/sessions",
        json={
            "source_language": "en",
            "target_language": "zh",
            "audio_source": {
                "type": "application",
                "bundle_id": "com.example.player",
                "process_id": 42,
                "display_name": "Example Player",
            },
        },
    ).json()


def test_application_audio_pause_resume_and_stop(settings, monkeypatch) -> None:
    fake_asr = FakeASRConnection()
    source = FakeNativeSource()
    monkeypatch.setattr(ASRProcessManager, "ensure_ready", no_wait)
    monkeypatch.setattr("app.live.websockets.connect", lambda *args, **kwargs: fake_asr)
    monkeypatch.setattr(OllamaTranslator, "translate", fake_translate)

    async def fake_start(self, session):
        return source

    monkeypatch.setattr(AppAudioCaptureManager, "start", fake_start)
    app = create_app(settings)
    chunks = [b"\x01\x00" * 160, b"\x02\x00" * 160, b"\x03\x00" * 160]

    with TestClient(app) as client:
        session = application_session(client)
        with client.websocket_connect(f"/ws/live?session_id={session['id']}") as websocket:
            source.chunks.put_nowait(chunks[0])
            receive_until(websocket, "capture_ready")
            websocket.send_json({"type": "pause"})
            receive_until_status(websocket, "paused")
            source.chunks.put_nowait(chunks[1])
            deadline = time.monotonic() + 2
            while len(fake_asr.audio) < 640 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert fake_asr.audio[320:640] == b"\x00" * 320

            websocket.send_json({"type": "resume"})
            source.chunks.put_nowait(chunks[2])
            deadline = time.monotonic() + 2
            while len(fake_asr.audio) < 960 and time.monotonic() < deadline:
                time.sleep(0.01)
            websocket.send_json({"type": "stop"})
            receive_until(websocket, "session_completed")

        assert bytes(fake_asr.audio) == chunks[0] + b"\x00" * 320 + chunks[2]
        assert source.stopped
        assert app.state.storage.get_session(session["id"])["status"] == "completed"


def test_application_audio_error_drains_existing_transcript(settings, monkeypatch) -> None:
    fake_asr = FakeASRConnection()
    source = FakeNativeSource()
    monkeypatch.setattr(ASRProcessManager, "ensure_ready", no_wait)
    monkeypatch.setattr("app.live.websockets.connect", lambda *args, **kwargs: fake_asr)
    monkeypatch.setattr(OllamaTranslator, "translate", fake_translate)

    async def fake_start(self, session):
        return source

    monkeypatch.setattr(AppAudioCaptureManager, "start", fake_start)
    app = create_app(settings)

    with TestClient(app) as client:
        session = application_session(client)
        with client.websocket_connect(f"/ws/live?session_id={session['id']}") as websocket:
            source.chunks.put_nowait(b"\x01\x00" * 160)
            receive_until(websocket, "capture_ready")
            source.chunks.put_nowait(AppAudioError("目标应用已退出"))
            assert receive_until(websocket, "capture_error")["message"] == "目标应用已退出"
            receive_until(websocket, "session_completed")

        detail = app.state.storage.get_session_detail(session["id"])
        assert detail["status"] == "completed"
        assert detail["segments"][0]["source_text"] == "Hello"
        assert detail["segments"][0]["translated_text"] == "你好"
