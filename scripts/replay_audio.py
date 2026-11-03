import argparse
import asyncio
import json
import subprocess
import time
from urllib.parse import urlencode

import websockets

from app.asr import ASRProcessManager
from app.config import get_settings


async def main() -> None:
    parser = argparse.ArgumentParser(description="Replay audio through standalone WhisperLiveKit")
    parser.add_argument("audio", help="Audio or video file readable by FFmpeg")
    parser.add_argument("--language", default="auto")
    parser.add_argument("--speed", type=float, default=1.0, help="0 feeds as fast as possible")
    args = parser.parse_args()

    settings = get_settings()
    manager = ASRProcessManager(settings)
    await manager.start()
    process = None
    try:
        await manager.ensure_ready()
        query = urlencode({"language": args.language, "mode": "full"})
        started = time.perf_counter()
        seen = set()
        committed = []

        async with websockets.connect(
            f"{settings.asr_ws_url}?{query}", max_size=16 * 1024 * 1024
        ) as websocket:

            async def receive_results():
                async for raw in websocket:
                    message = json.loads(raw)
                    if message.get("type") == "ready_to_stop":
                        return
                    for line in message.get("lines", []):
                        text = (line.get("text") or "").strip()
                        key = (line.get("start"), line.get("end"), text)
                        if text and key not in seen:
                            seen.add(key)
                            committed.append(line)
                            print(f"[{line.get('start')} → {line.get('end')}] {text}")

            reader = asyncio.create_task(receive_results())
            process = await asyncio.to_thread(
                subprocess.Popen,
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    args.audio,
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "pipe:1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            while True:
                chunk = await asyncio.to_thread(process.stdout.read, 3200)
                if not chunk:
                    break
                await websocket.send(chunk)
                if args.speed > 0:
                    await asyncio.sleep(0.1 / args.speed)
            await websocket.send(b"")
            await reader
            stderr = await asyncio.to_thread(process.stderr.read)
            return_code = await asyncio.to_thread(process.wait)
            if return_code != 0:
                raise RuntimeError(stderr.decode(errors="replace"))

        print(
            json.dumps(
                {
                    "asr_backend": settings.asr_backend,
                    "asr_model": settings.asr_model,
                    "segments": len(committed),
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "lines": committed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            await asyncio.to_thread(process.wait)
        await manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
