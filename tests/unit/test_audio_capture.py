import pytest

from app.audio_capture import AppAudioCaptureManager, AppAudioError


@pytest.fixture
def fake_helper(tmp_path):
    helper = tmp_path / "fake-app-audio-helper"
    helper.write_text(
        """#!/usr/bin/env python3
import json
import sys
import time

command = sys.argv[1]
if command == "permissions":
    print(json.dumps({"screen_recording_granted": True, "requested": "--request" in sys.argv}))
elif command == "list-apps":
    print(json.dumps([{"name": "Fake App", "bundle_id": "example.fake", "process_id": 123}]))
elif command == "capture":
    print("Capture started for Fake App", file=sys.stderr, flush=True)
    while True:
        sys.stdout.buffer.write(b"\\x01\\x00" * 160)
        sys.stdout.buffer.flush()
        time.sleep(0.01)
""",
        encoding="utf-8",
    )
    helper.chmod(0o755)
    return helper


@pytest.mark.asyncio
async def test_native_helper_commands_and_pcm_lifecycle(settings, fake_helper) -> None:
    settings = settings.model_copy(update={"app_audio_helper_path": fake_helper})
    manager = AppAudioCaptureManager(settings)

    assert await manager.permissions() == {
        "screen_recording_granted": True,
        "requested": False,
    }
    assert await manager.applications() == [
        {"name": "Fake App", "bundle_id": "example.fake", "process_id": 123}
    ]
    source = await manager.start(
        {
            "audio_source_bundle_id": "example.fake",
            "audio_source_process_id": 123,
        }
    )
    assert await source.read() == b"\x01\x00" * 160
    await source.stop()
    await manager.close()

    assert source.process.returncode is not None
    assert "Capture started for Fake App" in (
        settings.data_dir / "logs" / "app-audio.log"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_native_helper_requires_application_identity(settings, fake_helper) -> None:
    manager = AppAudioCaptureManager(
        settings.model_copy(update={"app_audio_helper_path": fake_helper})
    )
    with pytest.raises(AppAudioError, match="缺少目标应用标识"):
        await manager.start({})
