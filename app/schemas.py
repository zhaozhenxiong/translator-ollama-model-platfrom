from typing import Literal

from pydantic import BaseModel, Field, model_validator

TranslationStatus = Literal["pending", "translating", "done", "failed", "skipped"]
TranslationProvider = Literal["ollama", "cc_switch"]
ExportView = Literal["bilingual", "source", "translation"]


class AudioSource(BaseModel):
    type: Literal["microphone", "application"] = "microphone"
    bundle_id: str | None = Field(default=None, max_length=200)
    process_id: int | None = Field(default=None, gt=0)
    display_name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_application(self):
        if self.type == "application" and (not self.bundle_id or not self.process_id):
            raise ValueError("应用音频需要 bundle_id 和 process_id")
        return self


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    source_language: str = Field(default="auto", max_length=20)
    target_language: str = Field(default="zh", max_length=20)
    translation_provider: TranslationProvider = "ollama"
    translation_model: str | None = Field(default=None, min_length=1, max_length=200)
    audio_source: AudioSource = Field(default_factory=AudioSource)


class Session(BaseModel):
    id: str
    title: str
    source_language: str
    target_language: str
    asr_backend: str
    asr_model: str
    ollama_model: str
    translation_provider: TranslationProvider
    translation_model: str
    audio_source_type: Literal["microphone", "application"]
    audio_source_bundle_id: str | None
    audio_source_process_id: int | None
    audio_source_display_name: str | None
    created_at: str
    updated_at: str
    ended_at: str | None
    status: str


class Segment(BaseModel):
    id: str
    session_id: str
    sequence: int
    start_ms: int
    end_ms: int
    source_text: str
    translated_text: str | None
    detected_language: str | None
    translation_status: TranslationStatus
    error_message: str | None
    created_at: str
    updated_at: str


class SessionDetail(Session):
    segments: list[Segment]


class SessionPatch(BaseModel):
    title: str = Field(min_length=1, max_length=200)
