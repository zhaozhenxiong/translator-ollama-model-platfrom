import pytest

from app.config import Settings
from app.storage import Storage


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        asr_auto_start=False,
        asr_startup_timeout=0.05,
        translation_max_attempts=1,
    )


@pytest.fixture
def storage(settings: Settings):
    settings.ensure_directories()
    database = Storage(settings.data_dir / "test.db")
    yield database
    database.close()
