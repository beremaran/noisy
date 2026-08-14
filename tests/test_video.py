from __future__ import annotations

from noisy.video import build_ffmpeg_command, parse_resolution


def test_mp4_ffmpeg_command_uses_synthetic_black_source_and_aac() -> None:
    command = build_ffmpeg_command(
        "master.flac",
        "black.mp4",
        duration_seconds=3600.0,
        resolution=(1920, 1080),
        fps=1.0,
    )
    assert "color=c=black:s=1920x1080:r=1" in command
    assert "-c:v" in command and command[command.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" in command and command[command.index("-pix_fmt") + 1] == "yuv420p"
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "aac"
    assert "-b:a" in command and command[command.index("-b:a") + 1] == "256k"
    assert "+faststart" in command
    assert "-shortest" not in command


def test_mkv_ffmpeg_command_defaults_to_lossless_flac_audio() -> None:
    command = build_ffmpeg_command(
        "master.flac",
        "black.mkv",
        duration_seconds=10.5,
        resolution=(1280, 720),
        fps=30.0,
    )
    assert "color=c=black:s=1280x720:r=30" in command
    assert command[command.index("-c:a") + 1] == "flac"
    assert "+faststart" not in command


def test_resolution_parser() -> None:
    assert parse_resolution("1920x1080") == (1920, 1080)
