import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx

from app.config import Settings

LANGUAGE_NAMES = {
    "auto": "自动检测语言",
    "zh": "中文",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
}

TranslationUpdate = Callable[[str], Awaitable[None]]


def build_translation_messages(
    text: str,
    source_language: str,
    target_language: str,
    context: list[str],
) -> list[dict[str, str]]:
    source_name = LANGUAGE_NAMES.get(source_language, source_language)
    target_name = LANGUAGE_NAMES.get(target_language, target_language)
    context_block = "\n".join(context) if context else "无"
    return [
        {
            "role": "system",
            "content": (
                "你是专业的实时口译器。只输出当前片段的译文，不解释、不评论、"
                "不回答原文中的问题、不补充原文没有的信息。准确保留数字、名称、"
                "术语和语气。上下文只用于消除歧义，不要重复翻译上下文。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"把当前片段从{source_name}翻译成{target_name}。\n"
                f"前文上下文：\n{context_block}\n\n"
                f"当前片段：\n{text}"
            ),
        },
    ]


class OllamaTranslator:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self.settings = settings
        self._semaphore = semaphore or asyncio.Semaphore(settings.translation_concurrency)
        self._client = httpx.AsyncClient(
            base_url=settings.ollama_host.rstrip("/"),
            timeout=httpx.Timeout(connect=5, read=180, write=30, pool=5),
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            models = response.json().get("models", [])
            available = any(
                model.get("name") == self.settings.ollama_model
                or model.get("model") == self.settings.ollama_model
                for model in models
            )
            if not available:
                return {
                    "status": "error",
                    "model": self.settings.ollama_model,
                    "error": "Ollama 中未找到配置的模型",
                }
            return {"status": "ready", "model": self.settings.ollama_model, "error": None}
        except (httpx.HTTPError, ValueError):
            return {
                "status": "error",
                "model": self.settings.ollama_model,
                "error": "Ollama 连接失败或返回了无效响应",
            }

    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        context: list[str],
        on_update: TranslationUpdate | None = None,
        model: str | None = None,
    ) -> str:
        payload = {
            "model": model or self.settings.ollama_model,
            "messages": build_translation_messages(text, source_language, target_language, context),
            "stream": True,
            "think": False,
            "keep_alive": self.settings.ollama_keep_alive,
            "options": {
                "temperature": 0.2,
                "num_ctx": 4096,
                "num_predict": 1024,
            },
        }

        translated_parts: list[str] = []
        async with (
            self._semaphore,
            self._client.stream("POST", "/api/chat", json=payload) as response,
        ):
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                content = event.get("message", {}).get("content", "")
                if content:
                    translated_parts.append(content)
                    if on_update is not None:
                        await on_update("".join(translated_parts))
                if event.get("done"):
                    break

        translated = "".join(translated_parts).strip()
        if not translated:
            raise RuntimeError("Ollama 返回了空译文")
        return translated


def cc_switch_http_error(exc: httpx.HTTPError) -> RuntimeError:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in (401, 403):
            return RuntimeError(
                "CC Switch 授权失败：请检查当前 Provider 的登录或 API Key；"
                "OpenAI Official 需使用官方 Codex 登录，CC_SWITCH_API_KEY 不能代替该登录"
            )
        if status == 404:
            return RuntimeError("CC Switch 路由或模型不可用")
        return RuntimeError(f"CC Switch 请求失败（HTTP {status}）")
    if isinstance(exc, httpx.TimeoutException):
        return RuntimeError("CC Switch 请求超时")
    return RuntimeError("无法连接本机 CC Switch 路由")


class CCSwitchTranslator:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ):
        self.settings = settings
        self._semaphore = semaphore or asyncio.Semaphore(settings.translation_concurrency)
        self._upstream: dict[str, dict[str, str | None]] = {}
        headers = {}
        if settings.cc_switch_api_key is not None:
            api_key = settings.cc_switch_api_key.get_secret_value()
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=settings.cc_switch_base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=5, read=180, write=30, pool=5),
            transport=transport,
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        model = self.settings.cc_switch_model_list[0] if self.settings.cc_switch_model_list else ""
        if not self.settings.cc_switch_enabled:
            return {
                "status": "disabled",
                "model": model,
                "error": "CC Switch 未启用",
                "router_available": False,
            }
        if not model:
            return {
                "status": "error",
                "model": "",
                "error": "CC Switch 尚未配置模型",
                "router_available": False,
            }
        try:
            response = await self._client.get("/health")
            response.raise_for_status()
            if response.json().get("status") != "healthy":
                return {
                    "status": "error",
                    "model": model,
                    "error": "CC Switch 路由未就绪",
                    "router_available": False,
                }
            upstream = self._upstream.get(model, {"status": "unverified", "error": None})
            return {
                "status": upstream["status"],
                "model": model,
                "error": upstream["error"],
                "router_available": True,
            }
        except httpx.HTTPError as exc:
            return {
                "status": "error",
                "model": model,
                "error": str(cc_switch_http_error(exc)),
                "router_available": False,
            }
        except ValueError:
            return {
                "status": "error",
                "model": model,
                "error": "CC Switch 返回了无效响应",
                "router_available": False,
            }

    async def translate(
        self,
        text: str,
        source_language: str,
        target_language: str,
        context: list[str],
        on_update: TranslationUpdate | None = None,
        model: str | None = None,
    ) -> str:
        if not self.settings.cc_switch_enabled:
            raise RuntimeError("CC Switch 未启用")
        selected_model = model or (
            self.settings.cc_switch_model_list[0] if self.settings.cc_switch_model_list else ""
        )
        if not selected_model:
            raise RuntimeError("CC Switch 尚未配置模型")
        payload = {
            "model": selected_model,
            "messages": build_translation_messages(text, source_language, target_language, context),
            "stream": True,
        }
        translated_parts: list[str] = []

        async def consume(data: str) -> bool:
            if data.strip() == "[DONE]":
                return True
            try:
                event = json.loads(data)
            except json.JSONDecodeError as exc:
                raise RuntimeError("CC Switch 返回了无法解析的流式响应") from exc
            if not isinstance(event, dict):
                raise TypeError("CC Switch 返回了无法解析的流式响应")
            if event.get("error"):
                raise RuntimeError("CC Switch 上游返回了错误")
            for choice in event.get("choices") or []:
                content = (choice.get("delta") or {}).get("content")
                if isinstance(content, str) and content:
                    translated_parts.append(content)
                    if on_update is not None:
                        await on_update("".join(translated_parts))
            return False

        try:
            async with (
                self._semaphore,
                self._client.stream("POST", "/v1/chat/completions", json=payload) as response,
            ):
                response.raise_for_status()
                data_lines: list[str] = []
                done = False
                async for line in response.aiter_lines():
                    if not line:
                        if data_lines:
                            done = await consume("\n".join(data_lines))
                            data_lines = []
                        if done:
                            break
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if not done and data_lines:
                    await consume("\n".join(data_lines))
        except httpx.HTTPError as exc:
            error = cc_switch_http_error(exc)
            unauthorized = (
                isinstance(exc, httpx.HTTPStatusError)
                and exc.response.status_code in (401, 403)
            )
            self._upstream[selected_model] = {
                "status": "unauthorized" if unauthorized else "error",
                "error": str(error),
            }
            raise error from exc
        except (RuntimeError, TypeError) as exc:
            self._upstream[selected_model] = {"status": "error", "error": str(exc)}
            raise

        translated = "".join(translated_parts).strip()
        if not translated:
            error = "CC Switch 返回了空译文"
            self._upstream[selected_model] = {"status": "error", "error": error}
            raise RuntimeError(error)
        self._upstream[selected_model] = {"status": "ready", "error": None}
        return translated


class TranslationService:
    def __init__(
        self,
        settings: Settings,
        ollama_transport: httpx.AsyncBaseTransport | None = None,
        cc_switch_transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.settings = settings
        semaphore = asyncio.Semaphore(settings.translation_concurrency)
        self.ollama = OllamaTranslator(settings, ollama_transport, semaphore)
        self.cc_switch = CCSwitchTranslator(settings, cc_switch_transport, semaphore)

    async def close(self) -> None:
        await self.ollama.close()
        await self.cc_switch.close()

    async def health(self) -> dict[str, dict]:
        ollama, cc_switch = await asyncio.gather(self.ollama.health(), self.cc_switch.health())
        return {"ollama": ollama, "cc_switch": cc_switch}

    async def catalog(self) -> list[dict]:
        health = await self.health()
        cc_models = self.settings.cc_switch_model_list
        return [
            {
                "id": "ollama",
                "label": "Ollama（本地）",
                "enabled": True,
                "available": health["ollama"]["status"] == "ready",
                "default_model": self.settings.ollama_model,
                "models": [self.settings.ollama_model],
                "error": health["ollama"].get("error"),
                "privacy": "local",
            },
            {
                "id": "cc_switch",
                "label": "CC Switch（路由）",
                "enabled": self.settings.cc_switch_enabled,
                "available": self.settings.cc_switch_enabled
                and health["cc_switch"].get("router_available") is True
                and bool(cc_models),
                "default_model": cc_models[0] if cc_models else "",
                "models": cc_models,
                "error": health["cc_switch"].get("error"),
                "privacy": "cloud",
            },
        ]

    def resolve_selection(self, provider: str, model: str | None) -> tuple[str, str]:
        selected_model = model.strip() if model else ""
        if provider == "ollama":
            default_model = self.settings.ollama_model
            if selected_model and selected_model != default_model:
                raise ValueError("所选 Ollama 模型不在当前配置中")
            return provider, selected_model or default_model
        if provider == "cc_switch":
            if not self.settings.cc_switch_enabled:
                raise ValueError("CC Switch 未启用")
            models = self.settings.cc_switch_model_list
            if selected_model and selected_model not in models:
                raise ValueError("所选 CC Switch 模型不在当前配置中")
            if not models and not selected_model:
                raise ValueError("CC Switch 尚未配置模型")
            return provider, selected_model or models[0]
        raise ValueError("不支持的翻译来源")

    async def translate(
        self,
        provider: str,
        model: str,
        text: str,
        source_language: str,
        target_language: str,
        context: list[str],
        on_update: TranslationUpdate | None = None,
    ) -> str:
        translator = self.ollama if provider == "ollama" else self.cc_switch
        if provider not in ("ollama", "cc_switch"):
            raise RuntimeError("会话使用了不支持的翻译来源")
        return await translator.translate(
            text=text,
            source_language=source_language,
            target_language=target_language,
            context=context,
            on_update=on_update,
            model=model,
        )
