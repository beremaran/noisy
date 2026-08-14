"""Synthetic black-video generation and audio muxing through FFmpeg."""

from __future__ import annotations

from pathlib import Path
import math
import shutil
import subprocess


class VideoError(ValueError):
    """Raised when FFmpeg cannot be invoked or muxing fails."""


def parse_resolution(value: str) -> tuple[int, int]:
    """Parse a positive ``WIDTHxHEIGHT`` resolution."""

    if not isinstance(value, str) or value.count("x") != 1:
        raise VideoError("resolution must look like WIDTHxHEIGHT, for example 1920x1080")
    width_text, height_text = (part.strip() for part in value.split("x", 1))
    try:
        width = int(width_text)
        height = int(height_text)
    except ValueError as exc:
        raise VideoError("resolution width and height must be integers") from exc
    if width <= 0 or height <= 0:
        raise VideoError("resolution width and height must be greater than zero")
    return width, height


def video_container_for_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in {".mp4", ".mkv"}:
        raise VideoError(f"unsupported video output {path!s}; use .mp4 or .mkv")
    return suffix[1:]


def _format_fps(fps: float) -> str:
    if abs(fps - round(fps)) < 1e-9:
        return str(int(round(fps)))
    return format(fps, ".6f").rstrip("0").rstrip(".")


def build_ffmpeg_command(
    audio_input: str | Path,
    video_output: str | Path,
    *,
    duration_seconds: float,
    resolution: tuple[int, int] = (1920, 1080),
    fps: float = 1.0,
    video_codec: str = "libx264",
    audio_codec: str | None = None,
    ffmpeg_executable: str = "ffmpeg",
    overwrite: bool = False,
) -> list[str]:
    """Build a shell-free FFmpeg argv for a pure-black synthetic video."""

    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise VideoError("video duration must be finite and greater than zero")
    if len(resolution) != 2 or any(not isinstance(value, int) or value <= 0 for value in resolution):
        raise VideoError("video resolution must contain two positive integers")
    if not math.isfinite(fps) or fps <= 0:
        raise VideoError("video frame rate must be finite and greater than zero")
    if not isinstance(video_codec, str) or not video_codec.strip():
        raise VideoError("video codec must not be empty")

    container = video_container_for_path(video_output)
    resolved_audio_codec = audio_codec or ("flac" if container == "mkv" else "aac")
    if not isinstance(resolved_audio_codec, str) or not resolved_audio_codec.strip():
        raise VideoError("audio codec must not be empty")

    width, height = resolution
    color_source = f"color=c=black:s={width}x{height}:r={_format_fps(fps)}"
    command = [
        ffmpeg_executable,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y" if overwrite else "-n",
        "-f",
        "lavfi",
        "-i",
        color_source,
        "-i",
        str(audio_input),
        "-t",
        f"{duration_seconds:.6f}",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c:v",
        video_codec,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        resolved_audio_codec,
    ]
    if resolved_audio_codec.lower() == "aac":
        command.extend(["-b:a", "256k"])
    if container == "mp4":
        command.extend(["-movflags", "+faststart"])
    # ``-t`` is the authoritative duration.  Combining ``-shortest`` with a
    # deliberately low frame rate can make FFmpeg terminate on the last
    # complete video frame and truncate an otherwise valid AAC track (for
    # example, a 2.25 second request at 1 fps).  The finite ``-t`` bound keeps
    # the synthetic source from running forever while preserving the complete
    # audio duration.
    command.extend([str(video_output)])
    return command


def require_ffmpeg(executable: str = "ffmpeg") -> str:
    """Return the resolved FFmpeg executable or raise a useful error."""

    resolved = shutil.which(executable)
    if resolved is None:
        raise VideoError(
            f"FFmpeg executable {executable!r} was not found on PATH; "
            "install FFmpeg before requesting --video-output"
        )
    return resolved


def mux_black_video(
    audio_input: str | Path,
    video_output: str | Path,
    *,
    duration_seconds: float,
    resolution: tuple[int, int] = (1920, 1080),
    fps: float = 1.0,
    video_codec: str = "libx264",
    audio_codec: str | None = None,
    overwrite: bool = False,
    ffmpeg_executable: str = "ffmpeg",
) -> list[str]:
    """Run FFmpeg and return the exact command used."""

    resolved_ffmpeg = require_ffmpeg(ffmpeg_executable)
    output_path = Path(video_output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_ffmpeg_command(
        audio_input,
        output_path,
        duration_seconds=duration_seconds,
        resolution=resolution,
        fps=fps,
        video_codec=video_codec,
        audio_codec=audio_codec,
        ffmpeg_executable=resolved_ffmpeg,
        overwrite=overwrite,
    )
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise VideoError(f"could not start FFmpeg: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or "FFmpeg returned a non-zero exit status"
        raise VideoError(f"FFmpeg mux failed: {detail}")
    return command


__all__ = [
    "VideoError",
    "build_ffmpeg_command",
    "mux_black_video",
    "parse_resolution",
    "require_ffmpeg",
    "video_container_for_path",
]
