import argparse
import asyncio
import json
import subprocess
import time
from pathlib import Path

import httpx
import websockets


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay audio through the complete running application"
    )
    parser.add_argument("audio", help="Audio or video file readable by FFmpeg")
    parser.add_argument("--app", default="http://127.0.0.1:8000")
    parser.add_argument("--source", default="zh")
    parser.add_argument("--target", default="en")
    parser.add_argument("--provider", choices=["ollama", "cc_switch"], default="ollama")
    parser.add_argument("--model", help="Override the configured model for the selected provider")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--duration", type=float, help="Limit input duration in seconds")
    args = parser.parse_args()

    app_url = args.app.rstrip("/")
    ws_url = app_url.replace("https://", "wss://").replace("http://", "ws://")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{app_url}/api/sessions",
            json={
                "title": f"回放测试：{Path(args.audio).stem}",
                "source_language": args.source,
                "target_language": args.target,
                "translation_provider": args.provider,
                "translation_model": args.model,
            },
        )
        response.raise_for_status()
        session = response.json()

    print(f"session_id={session['id']}")
    started = time.perf_counter()
    process = None
    segments = {}
    try:
        async with websockets.connect(
            f"{ws_url}/ws/live?session_id={session['id']}", max_size=16 * 1024 * 1024
        ) as websocket:

            async def receive_results():
                async for raw in websocket:
                    event = json.loads(raw)
                    event_type = event.get("type")
                    if event_type == "status":
                        print(f"status: {event.get('status')} {event.get('message') or ''}")
                    elif event_type == "segment":
                        segment = event["segment"]
                        segments[segment["id"]] = segment
                        print(f"原文 [{segment['sequence']}]: {segment['source_text']}")
                    elif event_type == "translation_final":
                        segment = event["segment"]
                        segments[segment["id"]] = segment
                        print(f"译文 [{segment['sequence']}]: {segment['translated_text']}")
                    elif event_type == "translation_error":
                        print(f"翻译失败: {event['message']}")
                    elif event_type == "error":
                        raise RuntimeError(event["message"])
                    elif event_type == "session_completed":
                        return

            reader = asyncio.create_task(receive_results())
            command = [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                args.audio,
            ]
            if args.duration:
                command.extend(["-t", str(args.duration)])
            command.extend(
                [
                    "-f",
                    "s16le",
                    "-acodec",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "pipe:1",
                ]
            )
            process = await asyncio.create_subprocess_exec(
                *command, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            while True:
                chunk = await process.stdout.read(3200)
                if not chunk:
                    break
                await websocket.send(chunk)
                if args.speed > 0:
                    await asyncio.sleep(0.1 / args.speed)
            await websocket.send(json.dumps({"type": "stop"}))
            await reader
            stderr = await process.stderr.read()
            return_code = await process.wait()
            if return_code != 0:
                raise RuntimeError(stderr.decode(errors="replace"))

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{app_url}/api/sessions/{session['id']}")
            response.raise_for_status()
            detail = response.json()
        failed = [
            segment for segment in detail["segments"] if segment["translation_status"] == "failed"
        ]
        print(
            json.dumps(
                {
                    "session_id": session["id"],
                    "segments": len(detail["segments"]),
                    "failed_translations": len(failed),
                    "elapsed_seconds": round(time.perf_counter() - started, 2),
                    "export": f"{app_url}/api/sessions/{session['id']}/export?format=docx",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()


if __name__ == "__main__":
    asyncio.run(main())
