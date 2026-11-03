import json

import httpx
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.translator import CCSwitchTranslator, OllamaTranslator, TranslationService


def ndjson(events: list[dict]) -> bytes:
    return ("\n".join(json.dumps(event, ensure_ascii=False) for event in events) + "\n").encode()


@pytest.mark.asyncio
async def test_ollama_streaming_translation(settings) -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson(
                [
                    {"message": {"content": "你好"}, "done": False},
                    {"message": {"content": "，世界"}, "done": False},
                    {"message": {"content": ""}, "done": True},
                ]
            ),
        )

    translator = OllamaTranslator(settings, transport=httpx.MockTransport(handler))
    updates = []

    async def on_update(text: str) -> None:
        updates.append(text)

    result = await translator.translate("Hello world", "en", "zh", ["Previous sentence"], on_update)

    assert result == "你好，世界"
    assert updates == ["你好", "你好，世界"]
    assert captured["think"] is False
    assert captured["model"] == settings.ollama_model
    assert captured["stream"] is True
    assert "Previous sentence" in captured["messages"][1]["content"]
    await translator.close()


@pytest.mark.asyncio
async def test_ollama_health(settings) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": settings.ollama_model}]})

    translator = OllamaTranslator(settings, transport=httpx.MockTransport(handler))
    assert (await translator.health())["status"] == "ready"
    await translator.close()


def sse(events: list[str]) -> bytes:
    return "".join(f"data: {event}\n\n" for event in events).encode()


@pytest.mark.asyncio
async def test_cc_switch_streaming_translation(settings) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content)
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b": keepalive\n\n"
                + sse(
                    [
                        json.dumps({"choices": [{"delta": {"content": "你好"}}]}),
                        json.dumps({"choices": []}),
                        json.dumps({"choices": [{"delta": {"content": "，世界"}}]}),
                        "[DONE]",
                    ]
                )
            ),
        )

    translator = CCSwitchTranslator(settings, transport=httpx.MockTransport(handler))
    updates = []

    async def on_update(text: str) -> None:
        updates.append(text)

    result = await translator.translate(
        "Hello world", "en", "zh", ["Previous sentence"], on_update, "gpt-5.5"
    )

    assert result == "你好，世界"
    assert updates == ["你好", "你好，世界"]
    assert captured["path"] == "/v1/chat/completions"
    assert captured["authorization"] is None
    assert captured["payload"]["model"] == "gpt-5.5"
    assert set(captured["payload"]) == {"model", "messages", "stream"}
    await translator.close()


@pytest.mark.asyncio
async def test_cc_switch_http_error_does_not_expose_upstream_body(settings) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "secret-upstream-detail"})

    translator = CCSwitchTranslator(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError) as caught:
        await translator.translate("Hello", "en", "zh", [])
    assert "授权" in str(caught.value)
    assert "secret-upstream-detail" not in str(caught.value)
    await translator.close()


@pytest.mark.asyncio
async def test_cc_switch_health_tracks_authorization_and_successful_retry(settings) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        attempts += 1
        if attempts == 1:
            return httpx.Response(401, json={"error": "secret-upstream-detail"})
        return httpx.Response(
            200,
            content=sse([json.dumps({"choices": [{"delta": {"content": "你好"}}]}), "[DONE]"]),
        )

    translator = CCSwitchTranslator(settings, transport=httpx.MockTransport(handler))
    initial = await translator.health()
    assert initial["status"] == "unverified"
    assert initial["router_available"] is True

    with pytest.raises(RuntimeError, match="OpenAI Official"):
        await translator.translate("Hello", "en", "zh", [])
    assert (await translator.health())["status"] == "unauthorized"

    assert await translator.translate("Hello", "en", "zh", []) == "你好"
    recovered = await translator.health()
    assert recovered["status"] == "ready"
    assert recovered["error"] is None
    await translator.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "message"),
    [
        (sse(["not-json"]), "无法解析"),
        (sse([json.dumps({"choices": []}), "[DONE]"]), "空译文"),
    ],
)
async def test_cc_switch_rejects_invalid_streams(settings, content, message) -> None:
    settings = settings.model_copy(update={"cc_switch_enabled": True})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content)

    translator = CCSwitchTranslator(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeError, match=message):
        await translator.translate("Hello", "en", "zh", [])
    await translator.close()


@pytest.mark.asyncio
async def test_translation_service_catalog_and_snapshot_routing(settings) -> None:
    settings = settings.model_copy(
        update={"cc_switch_enabled": True, "cc_switch_models": "gpt-5.5, routed-2"}
    )
    routed_payload = {}
    ollama_payload = {}

    def ollama_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": settings.ollama_model}]})
        ollama_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=ndjson([{"message": {"content": "本地"}, "done": True}]),
        )

    def cc_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "healthy"})
        routed_payload.update(json.loads(request.content))
        return httpx.Response(
            200,
            content=sse([json.dumps({"choices": [{"delta": {"content": "路由"}}]}), "[DONE]"]),
        )

    service = TranslationService(
        settings,
        ollama_transport=httpx.MockTransport(ollama_handler),
        cc_switch_transport=httpx.MockTransport(cc_handler),
    )
    catalog = await service.catalog()
    assert catalog[0]["available"] is True
    assert catalog[1]["available"] is True
    assert catalog[1]["models"] == ["gpt-5.5", "routed-2"]
    assert "base_url" not in catalog[1]
    assert service.ollama._semaphore is service.cc_switch._semaphore

    result = await service.translate("cc_switch", "routed-2", "Hello", "en", "zh", [])
    assert result == "路由"
    assert routed_payload["model"] == "routed-2"
    assert await service.translate(
        "ollama", "legacy-ollama", "Hello", "en", "zh", []
    ) == "本地"
    assert ollama_payload["model"] == "legacy-ollama"
    await service.close()


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:15721",
        "http://example.com",
        "http://user:password@127.0.0.1:15721",
    ],
)
def test_cc_switch_requires_plain_loopback_http(url) -> None:
    with pytest.raises(ValidationError):
        Settings(cc_switch_base_url=url)
