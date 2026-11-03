from io import BytesIO

from docx import Document

from app.exporters import format_timestamp, render_docx, render_markdown, render_text
from app.schemas import SessionCreate
from app.storage import Storage


def sample(storage: Storage, settings) -> tuple[dict, list[dict]]:
    session = storage.create_session(
        SessionCreate(title="测试会议", source_language="en", target_language="zh"),
        settings,
    )
    storage.add_segment(session["id"], 1000, 2500, "Good morning.", "en")
    segments = storage.list_segments(session["id"])
    storage.update_translation(segments[0]["id"], "早上好。")
    return session, storage.list_segments(session["id"])


def test_markdown_and_text_views(storage: Storage, settings) -> None:
    session, segments = sample(storage, settings)
    bilingual = render_markdown(session, segments)
    source = render_markdown(session, segments, "source")
    translated = render_text(session, segments, "translation")

    assert "# 测试会议" in bilingual
    assert "原文：Good morning." in bilingual
    assert "译文：早上好。" in bilingual
    assert "译文：" not in source
    assert "早上好。" in translated
    assert "Good morning." not in translated
    assert f"Ollama / {settings.ollama_model}" in bilingual
    assert format_timestamp(3_661_000) == "01:01:01"


def test_docx_export(storage: Storage, settings) -> None:
    session, segments = sample(storage, settings)
    content = render_docx(session, segments)
    document = Document(BytesIO(content))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert content.startswith(b"PK")
    assert "测试会议" in text
    assert "Good morning." in text
    assert "早上好。" in text
    assert f"Ollama / {settings.ollama_model}" in text


def test_cc_switch_and_legacy_model_labels(storage: Storage, settings) -> None:
    session, segments = sample(storage, settings)
    session.update(translation_provider="cc_switch", translation_model="gpt-5.5")
    assert "CC Switch / gpt-5.5" in render_markdown(session, segments)

    session.pop("translation_provider")
    session.pop("translation_model")
    assert f"Ollama / {settings.ollama_model}" in render_markdown(session, segments)
