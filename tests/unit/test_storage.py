import sqlite3

from app.schemas import AudioSource, SessionCreate
from app.storage import Storage


def test_session_and_segment_round_trip(storage: Storage, settings) -> None:
    session = storage.create_session(
        SessionCreate(source_language="en", target_language="zh"), settings
    )
    first = storage.add_segment(session["id"], 0, 1200, "Hello", "en")
    second = storage.add_segment(session["id"], 1300, 2400, "How are you?", "en")

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert first["translation_status"] == "pending"

    storage.update_translation(first["id"], "你好")
    storage.fail_unfinished_translations(session["id"], "interrupted")
    detail = storage.get_session_detail(session["id"])

    assert detail["translation_provider"] == "ollama"
    assert detail["translation_model"] == settings.ollama_model
    assert detail["segments"][0]["translated_text"] == "你好"
    assert detail["segments"][0]["translation_status"] == "done"
    assert detail["segments"][1]["translation_status"] == "failed"
    assert detail["segments"][1]["source_text"] == "How are you?"

    storage.set_session_status(session["id"], "completed", ended=True)
    assert storage.get_session(session["id"])["ended_at"] is not None


def test_application_source_round_trip(storage: Storage, settings) -> None:
    session = storage.create_session(
        SessionCreate(
            audio_source=AudioSource(
                type="application",
                bundle_id="com.example.player",
                process_id=42,
                display_name="Example Player",
            )
        ),
        settings,
    )
    assert session["audio_source_type"] == "application"
    assert session["audio_source_bundle_id"] == "com.example.player"
    assert session["audio_source_process_id"] == 42
    assert session["audio_source_display_name"] == "Example Player"


def test_cc_switch_session_and_cascade_delete(storage: Storage, settings) -> None:
    session = storage.create_session(
        SessionCreate(translation_provider="cc_switch", translation_model="gpt-5.5"),
        settings,
    )
    storage.add_segment(session["id"], 0, 1000, "Hello", "en")

    assert session["translation_provider"] == "cc_switch"
    assert session["translation_model"] == "gpt-5.5"
    assert session["ollama_model"] == settings.ollama_model
    assert storage.delete_session(session["id"]) is True
    assert storage.get_session(session["id"]) is None
    assert storage.list_segments(session["id"]) == []
    assert storage.delete_session(session["id"]) is False


def test_interrupt_stale_sessions_preserves_completed_sessions(storage: Storage, settings) -> None:
    stale = storage.create_session(SessionCreate(), settings)
    completed = storage.create_session(SessionCreate(), settings)
    storage.set_session_status(stale["id"], "active")
    storage.set_session_status(completed["id"], "completed", ended=True)
    segment = storage.add_segment(stale["id"], 0, 1000, "Hello", "en")
    storage.set_translation_status(segment["id"], "translating")

    assert storage.interrupt_stale_sessions() == [stale["id"]]
    interrupted = storage.get_session(stale["id"])
    assert interrupted["status"] == "interrupted"
    assert interrupted["ended_at"] is not None
    assert storage.get_segment(segment["id"])["translation_status"] == "failed"
    assert storage.get_session(completed["id"])["status"] == "completed"
    assert storage.interrupt_stale_sessions() == []


def test_legacy_sessions_gain_microphone_source(settings) -> None:
    path = settings.data_dir / "legacy.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as database:
        database.execute(
            """
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                source_language TEXT NOT NULL,
                target_language TEXT NOT NULL,
                asr_backend TEXT NOT NULL,
                asr_model TEXT NOT NULL,
                ollama_model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL
            )
            """
        )
        database.execute(
            """
            INSERT INTO sessions VALUES (
                'legacy', 'Legacy', 'en', 'zh', 'mlx-whisper', 'large-v3-turbo',
                'qwen3.5:9b-mlx', '2026-10-31', '2026-10-31', NULL, 'completed'
            )
            """
        )
    migrated = Storage(path)
    try:
        session = migrated.get_session("legacy")
        assert session["audio_source_type"] == "microphone"
        assert session["audio_source_bundle_id"] is None
        assert session["audio_source_display_name"] is None
        assert session["translation_provider"] == "ollama"
        assert session["translation_model"] == "qwen3.5:9b-mlx"
    finally:
        migrated.close()
