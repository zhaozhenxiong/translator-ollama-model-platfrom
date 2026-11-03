import argparse
import asyncio
import json
import time

from app.config import get_settings
from app.translator import OllamaTranslator


async def main() -> None:
    parser = argparse.ArgumentParser(description="Verify the configured local Ollama translator")
    parser.add_argument("--text", default="Hello, how are you today?")
    parser.add_argument("--source", default="en")
    parser.add_argument("--target", default="zh")
    args = parser.parse_args()

    settings = get_settings()
    translator = OllamaTranslator(settings)
    try:
        health = await translator.health()
        print(json.dumps({"health": health}, ensure_ascii=False))
        if health["status"] != "ready":
            raise SystemExit(1)

        started = time.perf_counter()
        first_update = None

        async def on_update(_: str) -> None:
            nonlocal first_update
            if first_update is None:
                first_update = time.perf_counter()

        translated = await translator.translate(
            args.text,
            args.source,
            args.target,
            context=[],
            on_update=on_update,
        )
        finished = time.perf_counter()
        print(
            json.dumps(
                {
                    "model": settings.ollama_model,
                    "think": False,
                    "source": args.text,
                    "translation": translated,
                    "first_token_ms": round((first_update - started) * 1000)
                    if first_update
                    else None,
                    "total_ms": round((finished - started) * 1000),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        await translator.close()


if __name__ == "__main__":
    asyncio.run(main())
