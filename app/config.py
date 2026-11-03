import ipaddress
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = PROJECT_ROOT / "data"

    asr_auto_start: bool = True
    asr_host: str = "127.0.0.1"
    asr_port: int = 8765
    asr_backend: str = "mlx-whisper"
    asr_model: str = "large-v3-turbo"
    asr_language: str = "auto"
    asr_backend_policy: str = "localagreement"
    asr_pause_segmentation_seconds: float = 0.8
    asr_startup_timeout: float = 1800

    ollama_host: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3.5:9b-mlx"
    ollama_keep_alive: str = "30m"
    cc_switch_enabled: bool = False
    cc_switch_base_url: str = "http://127.0.0.1:15721"
    cc_switch_models: str = "gpt-5.5"
    cc_switch_api_key: SecretStr | None = None
    translation_concurrency: int = 1
    translation_queue_size: int = 8
    translation_max_attempts: int = 2

    app_audio_helper_path: Path | None = None
    app_audio_read_timeout: float = 10

    @field_validator("cc_switch_base_url")
    @classmethod
    def validate_cc_switch_base_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            loopback = ipaddress.ip_address(parsed.hostname or "").is_loopback
        except ValueError:
            loopback = (parsed.hostname or "").lower() == "localhost"
        if (
            parsed.scheme != "http"
            or not loopback
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("CC Switch 地址必须是本机 HTTP 回环源地址")
        return value.rstrip("/")

    @property
    def cc_switch_model_list(self) -> list[str]:
        return list(dict.fromkeys(model.strip() for model in self.cc_switch_models.split(",") if model.strip()))

    @property
    def asr_http_url(self) -> str:
        return f"http://{self.asr_host}:{self.asr_port}"

    @property
    def asr_ws_url(self) -> str:
        return f"ws://{self.asr_host}:{self.asr_port}/asr"

    @property
    def web_dist(self) -> Path:
        return PROJECT_ROOT / "web" / "dist"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "transcripts").mkdir(exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
