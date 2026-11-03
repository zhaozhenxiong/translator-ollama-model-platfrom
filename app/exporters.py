from io import BytesIO
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from app.schemas import ExportView


def format_timestamp(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def audio_source_label(session: dict) -> str:
    if session.get("audio_source_type") == "application":
        name = session.get("audio_source_display_name") or session.get("audio_source_bundle_id")
        return f"应用音频：{name}"
    return "麦克风 / BlackHole 输入"


def translation_model_label(session: dict) -> str:
    provider = session.get("translation_provider", "ollama")
    model = session.get("translation_model") or session.get("ollama_model") or "未知模型"
    provider_label = {"ollama": "Ollama", "cc_switch": "CC Switch"}.get(provider, provider)
    return f"{provider_label} / {model}"


def _segment_text(segment: dict, view: ExportView) -> list[str]:
    source = segment["source_text"]
    translation = segment["translated_text"] or "［翻译未完成］"
    if view == "source":
        return [source]
    if view == "translation":
        return [translation]
    return [f"原文：{source}", f"译文：{translation}"]


def render_markdown(session: dict, segments: list[dict], view: ExportView = "bilingual") -> str:
    lines = [
        f"# {session['title']}",
        "",
        f"- 创建时间：{session['created_at']}",
        f"- 源语言：{session['source_language']}",
        f"- 目标语言：{session['target_language']}",
        f"- 声音来源：{audio_source_label(session)}",
        f"- 听写模型：{session['asr_backend']} / {session['asr_model']}",
        f"- 翻译模型：{translation_model_label(session)}",
        "",
        "---",
        "",
    ]
    for segment in segments:
        lines.append(f"## {format_timestamp(segment['start_ms'])}")
        lines.append("")
        for text in _segment_text(segment, view):
            lines.extend([text, ""])
    return "\n".join(lines).rstrip() + "\n"


def render_text(session: dict, segments: list[dict], view: ExportView = "bilingual") -> str:
    lines = [
        session["title"],
        "=" * len(session["title"]),
        audio_source_label(session),
        f"翻译模型：{translation_model_label(session)}",
        "",
    ]
    for segment in segments:
        lines.append(f"[{format_timestamp(segment['start_ms'])}]")
        lines.extend(_segment_text(segment, view))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_docx(session: dict, segments: list[dict], view: ExportView = "bilingual") -> bytes:
    document = Document()
    document.core_properties.title = session["title"]
    document.add_heading(session["title"], level=0)
    document.add_paragraph(
        f"{session['created_at']} · {session['source_language']} → {session['target_language']}"
    )
    document.add_paragraph(audio_source_label(session))
    document.add_paragraph(f"翻译模型：{translation_model_label(session)}")
    style = document.styles["Normal"]
    style.font.name = "Arial"
    style.font.size = Pt(11)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "PingFang SC")

    for segment in segments:
        document.add_heading(format_timestamp(segment["start_ms"]), level=2)
        for text in _segment_text(segment, view):
            document.add_paragraph(text)

    output = BytesIO()
    document.save(output)
    return output.getvalue()


def markdown_mirror_path(data_dir: Path, session_id: str) -> Path:
    return data_dir / "transcripts" / f"{session_id}.md"


def delete_markdown_mirror(data_dir: Path, session_id: str) -> None:
    markdown_mirror_path(data_dir, session_id).unlink(missing_ok=True)


def write_markdown_mirror(data_dir: Path, session: dict, segments: list[dict]) -> Path:
    destination = markdown_mirror_path(data_dir, session["id"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(render_markdown(session, segments), encoding="utf-8")
    temporary.replace(destination)
    return destination
