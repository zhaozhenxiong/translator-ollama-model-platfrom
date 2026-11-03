import asyncio
import contextlib

import pytest

from app.live import LiveSession, parse_timestamp_ms
from app.schemas import SessionCreate
from app.storage import Storage


class FakeWebSocket:
    def __init__(self):
        self.events = []

    async def send_json(self, payload):
        self.events.append(payload)


class FakeTranslator:
    def __init__(self, error=False):
        self.error = error
        self.calls = []

    async def translate(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError("model unavailable")
        await kwargs["on_update"]("你好")
        return "你好"


async def run_commit(storage: Storage, settings, translator, line: dict):
    session = storage.create_session(
        SessionCreate(source_language="auto", target_language="zh"), settings
    )
    websocket = FakeWebSocket()
    live = LiveSession(websocket, session, settings, storage, translator, None)
    worker = asyncio.create_task(live._translation_worker())
    try:
        await live._commit_line(line, {})
        await live._commit_line(line, {})
        await live.translation_queue.join()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    return session, websocket.events


@pytest.mark.asyncio
async def test_stable_line_is_saved_and_translated_once(storage: Storage, settings) -> None:
    translator = FakeTranslator()
    line = {
        "speaker": 1,
        "start": "0:00:00",
        "end": "0:00:01.500",
        "text": "Hello",
        "detected_language": "en",
    }
    session, events = await run_commit(storage, settings, translator, line)
    segments = storage.list_segments(session["id"])

    assert len(segments) == 1
    assert segments[0]["end_ms"] == 1500
    assert segments[0]["translated_text"] == "你好"
    assert len(translator.calls) == 1
    assert translator.calls[0]["provider"] == "ollama"
    assert translator.calls[0]["model"] == settings.ollama_model
    assert [event["type"] for event in events].index("segment") < [
        event["type"] for event in events
    ].index("translation_final")


@pytest.mark.asyncio
async def test_failed_translation_preserves_source(storage: Storage, settings) -> None:
    line = {"speaker": 1, "start": "0:00:00", "end": "0:00:01", "text": "Hello"}
    session, events = await run_commit(storage, settings, FakeTranslator(error=True), line)
    segment = storage.list_segments(session["id"])[0]

    assert segment["source_text"] == "Hello"
    assert segment["translation_status"] == "failed"
    assert segment["error_message"] == "model unavailable"
    assert any(event["type"] == "translation_error" for event in events)
    mirror = settings.data_dir / "transcripts" / f"{session['id']}.md"
    assert "Hello" in mirror.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_full_snapshots_do_not_create_cumulative_duplicate_segments(
    storage: Storage, settings
) -> None:
    session = storage.create_session(
        SessionCreate(source_language="en", target_language="zh"), settings
    )
    websocket = FakeWebSocket()
    translator = FakeTranslator()
    live = LiveSession(websocket, session, settings, storage, translator, None)
    worker = asyncio.create_task(live._translation_worker())
    growing = {
        "speaker": 1,
        "start": "0:00:00",
        "end": "0:00:02",
        "text": "Hello world",
        "detected_language": "en",
    }
    silence = {"speaker": -2, "start": "0:00:02", "end": "0:00:02.5", "text": ""}
    second = {
        "speaker": 1,
        "start": "0:00:02.5",
        "end": "0:00:03.5",
        "text": "Second sentence",
        "detected_language": "en",
    }
    try:
        await live._process_line_snapshot([{**growing, "text": "Hello"}], {})
        await live._process_line_snapshot([growing], {})
        assert storage.list_segments(session["id"]) == []

        await live._process_line_snapshot([growing, silence], {})
        await live._process_line_snapshot([growing, silence], {})
        await live._process_line_snapshot([growing, silence, second], {})
        assert [s["source_text"] for s in storage.list_segments(session["id"])] == [
            "Hello world"
        ]

        await live._flush_pending_line()
        await live.translation_queue.join()
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    segments = storage.list_segments(session["id"])
    assert [segment["source_text"] for segment in segments] == [
        "Hello world",
        "Second sentence",
    ]
    assert len(translator.calls) == 2
    partials = [event["text"] for event in websocket.events if event["type"] == "partial"]
    assert partials[-1] == "Second sentence"


def test_timestamp_parser() -> None:
    assert parse_timestamp_ms("1:02:03.5") == 3_723_500
    assert parse_timestamp_ms(2.25) == 2250
    assert parse_timestamp_ms(None) == 0
