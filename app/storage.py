import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.schemas import SessionCreate


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Storage:
    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(database_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_language TEXT NOT NULL,
                    target_language TEXT NOT NULL,
                    asr_backend TEXT NOT NULL,
                    asr_model TEXT NOT NULL,
                    ollama_model TEXT NOT NULL,
                    translation_provider TEXT NOT NULL DEFAULT 'ollama',
                    translation_model TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    ended_at TEXT,
                    status TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS segments (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    start_ms INTEGER NOT NULL,
                    end_ms INTEGER NOT NULL,
                    source_text TEXT NOT NULL,
                    translated_text TEXT,
                    detected_language TEXT,
                    translation_status TEXT NOT NULL,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(session_id, sequence)
                );

                CREATE INDEX IF NOT EXISTS idx_segments_session
                ON segments(session_id, sequence);
                """
            )
            session_columns = {
                row["name"] for row in self._db.execute("PRAGMA table_info(sessions)")
            }
            additions = {
                "translation_provider": "TEXT NOT NULL DEFAULT 'ollama'",
                "translation_model": "TEXT NOT NULL DEFAULT ''",
                "audio_source_type": "TEXT NOT NULL DEFAULT 'microphone'",
                "audio_source_bundle_id": "TEXT",
                "audio_source_process_id": "INTEGER",
                "audio_source_display_name": "TEXT",
            }
            for name, definition in additions.items():
                if name not in session_columns:
                    self._db.execute(f"ALTER TABLE sessions ADD COLUMN {name} {definition}")
            self._db.execute(
                """
                UPDATE sessions
                SET translation_model = ollama_model
                WHERE translation_model = ''
                """
            )
            self._db.commit()

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def create_session(
        self,
        request: SessionCreate,
        settings: Settings,
        translation_provider: str | None = None,
        translation_model: str | None = None,
    ) -> dict:
        session_id = uuid.uuid4().hex
        now = utc_now()
        title = request.title or f"会话 {datetime.now().astimezone():%Y-%m-%d %H:%M}"
        source = request.audio_source
        is_application = source.type == "application"
        provider = translation_provider or request.translation_provider
        model = translation_model or request.translation_model
        if model is None:
            if provider == "ollama":
                model = settings.ollama_model
            else:
                configured_models = settings.cc_switch_model_list
                if not configured_models:
                    raise ValueError("CC Switch 尚未配置模型")
                model = configured_models[0]
        with self._lock:
            self._db.execute(
                """
                INSERT INTO sessions (
                    id, title, source_language, target_language, asr_backend,
                    asr_model, ollama_model, translation_provider, translation_model,
                    audio_source_type, audio_source_bundle_id, audio_source_process_id,
                    audio_source_display_name, created_at, updated_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created')
                """,
                (
                    session_id,
                    title,
                    request.source_language,
                    request.target_language,
                    settings.asr_backend,
                    settings.asr_model,
                    settings.ollama_model,
                    provider,
                    model,
                    source.type,
                    source.bundle_id if is_application else None,
                    source.process_id if is_application else None,
                    (source.display_name or source.bundle_id) if is_application else None,
                    now,
                    now,
                ),
            )
            self._db.commit()
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return self._dict(row)

    def get_session_detail(self, session_id: str) -> dict | None:
        session = self.get_session(session_id)
        if session is None:
            return None
        session["segments"] = self.list_segments(session_id)
        return session

    def list_sessions(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM sessions ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            cursor = self._db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._db.commit()
        return cursor.rowcount > 0

    def update_session_title(self, session_id: str, title: str) -> dict | None:
        with self._lock:
            self._db.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, utc_now(), session_id),
            )
            self._db.commit()
        return self.get_session(session_id)

    def set_session_status(self, session_id: str, status: str, ended: bool = False) -> None:
        now = utc_now()
        with self._lock:
            if ended:
                self._db.execute(
                    "UPDATE sessions SET status = ?, updated_at = ?, ended_at = ? WHERE id = ?",
                    (status, now, now, session_id),
                )
            else:
                self._db.execute(
                    "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                    (status, now, session_id),
                )
            self._db.commit()

    def interrupt_stale_sessions(self) -> list[str]:
        now = utc_now()
        with self._lock:
            session_ids = [
                row["id"]
                for row in self._db.execute(
                    "SELECT id FROM sessions WHERE status = 'active'"
                ).fetchall()
            ]
            if not session_ids:
                return []
            placeholders = ",".join("?" for _ in session_ids)
            self._db.execute(
                f"""
                UPDATE sessions
                SET status = 'interrupted', updated_at = ?, ended_at = ?
                WHERE id IN ({placeholders})
                """,
                (now, now, *session_ids),
            )
            self._db.execute(
                f"""
                UPDATE segments
                SET translation_status = 'failed',
                    error_message = '应用重启，未完成翻译可手动重试', updated_at = ?
                WHERE session_id IN ({placeholders})
                    AND translation_status IN ('pending', 'translating')
                """,
                (now, *session_ids),
            )
            self._db.commit()
            return session_ids

    def list_segments(self, session_id: str) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM segments WHERE session_id = ? ORDER BY sequence", (session_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_segment(self, segment_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM segments WHERE id = ?", (segment_id,)).fetchone()
        return self._dict(row)

    def add_segment(
        self,
        session_id: str,
        start_ms: int,
        end_ms: int,
        source_text: str,
        detected_language: str | None,
    ) -> dict:
        segment_id = uuid.uuid4().hex
        now = utc_now()
        with self._lock:
            sequence = self._db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM segments WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            self._db.execute(
                """
                INSERT INTO segments (
                    id, session_id, sequence, start_ms, end_ms, source_text,
                    detected_language, translation_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    segment_id,
                    session_id,
                    sequence,
                    start_ms,
                    end_ms,
                    source_text,
                    detected_language,
                    now,
                    now,
                ),
            )
            self._db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            self._db.commit()
        return self.get_segment(segment_id)

    def set_translation_status(
        self, segment_id: str, status: str, error_message: str | None = None
    ) -> dict | None:
        with self._lock:
            self._db.execute(
                """
                UPDATE segments
                SET translation_status = ?, error_message = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error_message, utc_now(), segment_id),
            )
            self._db.commit()
        return self.get_segment(segment_id)

    def update_translation(self, segment_id: str, translated_text: str) -> dict | None:
        with self._lock:
            self._db.execute(
                """
                UPDATE segments
                SET translated_text = ?, translation_status = 'done',
                    error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (translated_text, utc_now(), segment_id),
            )
            self._db.commit()
        return self.get_segment(segment_id)

    def fail_unfinished_translations(self, session_id: str, message: str) -> None:
        with self._lock:
            self._db.execute(
                """
                UPDATE segments
                SET translation_status = 'failed', error_message = ?, updated_at = ?
                WHERE session_id = ? AND translation_status IN ('pending', 'translating')
                """,
                (message, utc_now(), session_id),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
