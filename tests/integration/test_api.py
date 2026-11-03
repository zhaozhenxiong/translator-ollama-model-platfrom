from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.main import create_app
from app.schemas import SessionCreate
from app.storage import Storage
from app.translator import TranslationService


def test_session_api_and_exports(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/sessions", json={"source_language": "en", "target_language": "zh"}
        )
        assert response.status_code == 201
        session = response.json()
        app.state.storage.add_segment(session["id"], 0, 1000, "Hello", "en")

        detail = client.get(f"/api/sessions/{session['id']}")
        assert detail.status_code == 200
        assert detail.json()["segments"][0]["source_text"] == "Hello"

        patched = client.patch(f"/api/sessions/{session['id']}", json={"title": "新标题"})
        assert patched.json()["title"] == "新标题"
        assert client.get("/api/sessions").json()[0]["id"] == session["id"]

        markdown = client.get(f"/api/sessions/{session['id']}/export?format=md")
        assert markdown.status_code == 200
        assert "Hello" in markdown.text
        assert markdown.headers["content-disposition"].endswith('.md"')

        docx = client.get(f"/api/sessions/{session['id']}/export?format=docx")
        assert docx.status_code == 200
        assert docx.content.startswith(b"PK")

        missing = client.get("/api/sessions/not-found")
        assert missing.status_code == 404


def test_safe_provider_config_and_model_validation(settings) -> None:
    settings = settings.model_copy(
        update={
            "cc_switch_enabled": True,
            "cc_switch_api_key": SecretStr("local-secret"),
            "cc_switch_models": "gpt-5.5, routed-2",
        }
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/api/config")
        assert response.status_code == 200
        providers = response.json()["translation_providers"]
        assert providers[1]["id"] == "cc_switch"
        assert providers[1]["available"] is True
        assert providers[1]["models"] == ["gpt-5.5", "routed-2"]
        assert "15721" not in response.text
        assert "local-secret" not in response.text

        invalid = client.post(
            "/api/sessions",
            json={"translation_provider": "cc_switch", "translation_model": "unknown"},
        )
        assert invalid.status_code == 400
        assert client.get("/api/sessions").json() == []


def test_startup_reconciles_stale_active_session(settings) -> None:
    settings.ensure_directories()
    storage = Storage(settings.data_dir / "app.db")
    session = storage.create_session(SessionCreate(title="遗留会话"), settings)
    storage.set_session_status(session["id"], "active")
    segment = storage.add_segment(session["id"], 0, 1000, "Hello", "en")
    storage.set_translation_status(segment["id"], "translating")
    storage.close()

    app = create_app(settings)
    with TestClient(app) as client:
        detail = client.get(f"/api/sessions/{session['id']}").json()
        assert detail["status"] == "interrupted"
        assert detail["ended_at"] is not None
        assert detail["segments"][0]["translation_status"] == "failed"
        assert "应用重启" in detail["segments"][0]["error_message"]
        mirror = settings.data_dir / "transcripts" / f"{session['id']}.md"
        assert mirror.exists()
        assert "翻译未完成" in mirror.read_text()


def test_delete_session_removes_segments_and_mirror_and_protects_active(settings) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()
        other = client.post("/api/sessions", json={}).json()
        app.state.storage.add_segment(session["id"], 0, 1000, "Hello", "en")
        mirror = settings.data_dir / "transcripts" / f"{session['id']}.md"
        assert mirror.exists()

        app.state.active_sessions.add(session["id"])
        active = client.delete(f"/api/sessions/{session['id']}")
        assert active.status_code == 409
        assert client.get(f"/api/sessions/{session['id']}").status_code == 200
        assert mirror.exists()
        app.state.active_sessions.discard(session["id"])

        deleted = client.delete(f"/api/sessions/{session['id']}")
        assert deleted.status_code == 204
        assert deleted.content == b""
        assert client.get(f"/api/sessions/{session['id']}").status_code == 404
        assert app.state.storage.list_segments(session["id"]) == []
        assert not mirror.exists()
        assert client.get(f"/api/sessions/{other['id']}").status_code == 200
        assert client.delete(f"/api/sessions/{session['id']}").status_code == 404


def test_mirror_failure_leaves_database_untouched(settings, monkeypatch) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        session = client.post("/api/sessions", json={}).json()

        def fail_delete(*args):
            raise OSError("disk busy")

        monkeypatch.setattr("app.main.delete_markdown_mirror", fail_delete)
        response = client.delete(f"/api/sessions/{session['id']}")
        assert response.status_code == 500
        assert client.get(f"/api/sessions/{session['id']}").status_code == 200


def test_retry_uses_stored_cc_switch_provider_and_model(settings, monkeypatch) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})
    app = create_app(settings)
    calls = []

    async def fake_translate(self, **kwargs):
        calls.append(kwargs)
        return "你好"

    monkeypatch.setattr(TranslationService, "translate", fake_translate)
    with TestClient(app) as client:
        session = client.post(
            "/api/sessions",
            json={"translation_provider": "cc_switch", "translation_model": "gpt-5.5"},
        ).json()
        segment = app.state.storage.add_segment(session["id"], 0, 1000, "Hello", "en")
        response = client.post(f"/api/segments/{segment['id']}/retry")

        assert response.status_code == 200
        assert response.json()["translated_text"] == "你好"
        assert calls[0]["provider"] == "cc_switch"
        assert calls[0]["model"] == "gpt-5.5"
