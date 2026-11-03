import asyncio
import sqlite3
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.asr import ASRProcessManager
from app.audio_capture import AppAudioCaptureManager, AppAudioError, AppAudioPermissionError
from app.config import Settings, get_settings
from app.exporters import (
    delete_markdown_mirror,
    render_docx,
    render_markdown,
    render_text,
    write_markdown_mirror,
)
from app.live import LiveSession
from app.schemas import Segment, Session, SessionCreate, SessionDetail, SessionPatch
from app.storage import Storage
from app.translator import TranslationService


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app_settings.ensure_directories()
        app.state.storage = Storage(app_settings.data_dir / "app.db")
        stale_session_ids = app.state.storage.interrupt_stale_sessions()
        app.state.translator = TranslationService(app_settings)
        app.state.asr_manager = ASRProcessManager(app_settings)
        app.state.audio_capture_manager = AppAudioCaptureManager(app_settings)
        for session_id in stale_session_ids:
            await mirror_session(session_id)
        await app.state.asr_manager.start()
        yield
        await app.state.audio_capture_manager.close()
        await app.state.asr_manager.stop()
        await app.state.translator.close()
        app.state.storage.close()

    app = FastAPI(
        title="Local Live Translator",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.active_sessions = set()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
    )

    def session_or_404(session_id: str) -> dict:
        session = app.state.storage.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

    async def mirror_session(session_id: str) -> None:
        detail = app.state.storage.get_session_detail(session_id)
        if detail is not None:
            segments = detail.pop("segments")
            await asyncio.to_thread(write_markdown_mirror, app_settings.data_dir, detail, segments)

    @app.get("/api/health")
    async def health() -> dict:
        asr_status = app.state.asr_manager.status
        translation_status = await app.state.translator.health()
        ready = asr_status["status"] == "ready" and any(
            status["status"] == "ready" for status in translation_status.values()
        )
        return {
            "status": "ready" if ready else "degraded",
            "asr": asr_status,
            **translation_status,
        }

    @app.get("/api/config")
    async def config() -> dict:
        return {
            "asr_backend": app_settings.asr_backend,
            "asr_model": app_settings.asr_model,
            "ollama_model": app_settings.ollama_model,
            "default_source_language": app_settings.asr_language,
            "default_target_language": "zh",
            "application_audio_available": app.state.audio_capture_manager.available(),
            "translation_providers": await app.state.translator.catalog(),
        }

    @app.get("/api/audio-sources/permissions")
    async def audio_permissions() -> dict:
        try:
            return await app.state.audio_capture_manager.permissions()
        except AppAudioError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/audio-sources/permissions/request")
    async def request_audio_permissions() -> dict:
        try:
            return await app.state.audio_capture_manager.permissions(request=True)
        except AppAudioError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.get("/api/audio-sources/applications")
    async def audio_applications() -> list[dict]:
        try:
            return await app.state.audio_capture_manager.applications()
        except AppAudioPermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except AppAudioError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/sessions", response_model=Session, status_code=201)
    async def create_session(request: SessionCreate) -> dict:
        try:
            provider, model = app.state.translator.resolve_selection(
                request.translation_provider, request.translation_model
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        providers = await app.state.translator.catalog()
        selected = next(item for item in providers if item["id"] == provider)
        if not selected["enabled"] or not selected["available"]:
            raise HTTPException(
                status_code=409,
                detail=selected.get("error") or "所选翻译来源当前不可用",
            )
        session = app.state.storage.create_session(request, app_settings, provider, model)
        await mirror_session(session["id"])
        return session

    @app.get("/api/sessions", response_model=list[Session])
    async def list_sessions() -> list[dict]:
        return app.state.storage.list_sessions()

    @app.get("/api/sessions/{session_id}/export")
    async def export_session(
        session_id: str,
        format: Literal["md", "txt", "docx"] = Query(default="md"),
        view: Literal["bilingual", "source", "translation"] = Query(default="bilingual"),
    ) -> Response:
        detail = app.state.storage.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        segments = detail.pop("segments")
        filename = f"transcript-{session_id[:8]}.{format}"
        if format == "docx":
            content = render_docx(detail, segments, view)
            media_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif format == "txt":
            content = render_text(detail, segments, view).encode("utf-8")
            media_type = "text/plain; charset=utf-8"
        else:
            content = render_markdown(detail, segments, view).encode("utf-8")
            media_type = "text/markdown; charset=utf-8"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(session_id: str) -> dict:
        detail = app.state.storage.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        return detail

    @app.patch("/api/sessions/{session_id}", response_model=Session)
    async def patch_session(session_id: str, request: SessionPatch) -> dict:
        session_or_404(session_id)
        updated = app.state.storage.update_session_title(session_id, request.title.strip())
        await mirror_session(session_id)
        return updated

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> Response:
        detail = app.state.storage.get_session_detail(session_id)
        if detail is None:
            raise HTTPException(status_code=404, detail="会话不存在")
        if session_id in app.state.active_sessions:
            raise HTTPException(status_code=409, detail="进行中的会话不能删除，请先结束听写")
        segments = detail.pop("segments")
        try:
            delete_markdown_mirror(app_settings.data_dir, session_id)
        except OSError as exc:
            raise HTTPException(
                status_code=500, detail="无法删除 Markdown，数据库记录未更改"
            ) from exc
        try:
            deleted = app.state.storage.delete_session(session_id)
        except sqlite3.Error as exc:
            try:
                write_markdown_mirror(app_settings.data_dir, detail, segments)
            except OSError as restore_exc:
                raise HTTPException(
                    status_code=500,
                    detail="数据库删除失败，Markdown 也未能恢复，请检查本地文件",
                ) from restore_exc
            raise HTTPException(status_code=500, detail="删除会话失败，记录未更改") from exc
        if not deleted:
            write_markdown_mirror(app_settings.data_dir, detail, segments)
            raise HTTPException(status_code=404, detail="会话不存在")
        return Response(status_code=204)

    @app.post("/api/segments/{segment_id}/retry", response_model=Segment)
    async def retry_translation(segment_id: str) -> dict:
        segment = app.state.storage.get_segment(segment_id)
        if segment is None:
            raise HTTPException(status_code=404, detail="片段不存在")
        session = session_or_404(segment["session_id"])
        previous_segments = [
            item["source_text"]
            for item in app.state.storage.list_segments(session["id"])
            if item["sequence"] < segment["sequence"]
        ][-2:]
        app.state.storage.set_translation_status(segment_id, "translating")
        try:
            translated = await app.state.translator.translate(
                provider=session.get("translation_provider", "ollama"),
                model=session.get("translation_model") or session["ollama_model"],
                text=segment["source_text"],
                source_language=segment.get("detected_language") or session["source_language"],
                target_language=session["target_language"],
                context=previous_segments,
            )
            updated = app.state.storage.update_translation(segment_id, translated)
            await mirror_session(session["id"])
            return updated
        except Exception as exc:
            app.state.storage.set_translation_status(segment_id, "failed", str(exc))
            await mirror_session(session["id"])
            raise HTTPException(status_code=502, detail=f"翻译失败：{exc}") from exc

    @app.websocket("/ws/live")
    async def live(websocket: WebSocket, session_id: str = Query()) -> None:
        session = app.state.storage.get_session(session_id)
        if session is None:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "会话不存在"})
            await websocket.close(code=1008)
            return
        if session_id in app.state.active_sessions:
            await websocket.accept()
            await websocket.send_json({"type": "error", "message": "该会话已在录制中"})
            await websocket.close(code=1008)
            return
        app.state.active_sessions.add(session_id)
        try:
            live_session = LiveSession(
                websocket=websocket,
                session=session,
                settings=app_settings,
                storage=app.state.storage,
                translator=app.state.translator,
                asr_manager=app.state.asr_manager,
                capture_manager=app.state.audio_capture_manager,
            )
            await live_session.run()
        finally:
            app.state.active_sessions.discard(session_id)

    if app_settings.web_dist.exists():
        app.mount("/", StaticFiles(directory=app_settings.web_dist, html=True), name="web")
    else:

        @app.get("/")
        async def root() -> dict:
            return {
                "name": "Local Live Translator",
                "status": "backend ready; frontend has not been built",
                "health": "/api/health",
            }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
