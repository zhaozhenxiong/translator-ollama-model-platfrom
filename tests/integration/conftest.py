import pytest

from app.translator import TranslationService


@pytest.fixture(autouse=True)
def fake_translation_health(monkeypatch):
    async def health(self):
        cc_ready = self.settings.cc_switch_enabled
        return {
            "ollama": {
                "status": "ready",
                "model": self.settings.ollama_model,
                "error": None,
            },
            "cc_switch": {
                "status": "unverified" if cc_ready else "disabled",
                "model": self.settings.cc_switch_model_list[0]
                if self.settings.cc_switch_model_list
                else "",
                "error": None if cc_ready else "CC Switch 未启用",
                "router_available": cc_ready,
            },
        }

    monkeypatch.setattr(TranslationService, "health", health)
