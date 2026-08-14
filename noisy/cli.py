"""Command-line interface for streaming colored-noise generation."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from .audio import AudioError, validate_audio_output
from .config import AudioConfig, ConfigurationError, calculate_duration
from .mixer import MixError, parse_mix
from .pipeline import generate_audio
from .video import VideoError, mux_black_video, parse_resolution, require_ffmpeg
from . import __version__


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _nonnegative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _resolve_output_cutoffs(
    sample_rate: int,
    highpass_hz: float | None,
    lowpass_hz: float | None,
) -> tuple[float | None, float | None]:
    """Resolve CLI omissions and map explicit zeroes to disabled filters."""

    safe_default_highpass = min(20.0, 0.1 * sample_rate)
    safe_default_lowpass = min(17_000.0, 0.45 * sample_rate)
    return (
        None if highpass_hz == 0 else highpass_hz or safe_default_highpass,
        None if lowpass_hz == 0 else lowpass_hz or safe_default_lowpass,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate long-duration streaming colored-noise audio and optionally "
            "mux it with a pure-black FFmpeg video."
        )
    )
    parser.add_argument(
        "--mix",
        required=True,
        help="comma-separated power ratios, such as brown=60,pink=30,white=10",
    )
    parser.add_argument("--hours", type=_nonnegative_float, default=0.0)
    parser.add_argument("--minutes", type=_nonnegative_float, default=0.0)
    parser.add_argument("--seconds", type=_nonnegative_float, default=0.0)
    parser.add_argument("--sample-rate", type=_positive_int, default=48_000)
    parser.add_argument("--channels", type=_positive_int, choices=(1, 2), default=1)
    parser.add_argument(
        "--chunk-seconds",
        type=_positive_float,
        default=5.0,
        help="streaming block duration; 5 seconds is the default",
    )
    parser.add_argument(
        "--target-rms-db",
        type=float,
        default=-18.0,
        help="fixed master RMS target in dBFS (default: -18)",
    )
    parser.add_argument(
        "--peak-ceiling",
        type=_positive_float,
        default=0.98,
        help="linear full-scale peak ceiling (default: 0.98)",
    )
    parser.add_argument(
        "--brown-highpass-hz",
        type=_positive_float,
        default=12.0,
        help="brown-noise drift-control high-pass corner (default: 12 Hz)",
    )
    parser.add_argument(
        "--highpass-hz",
        type=_nonnegative_float,
        default=None,
        help="output high-pass corner; defaults to 20 Hz (sample-rate-safe), zero disables",
    )
    parser.add_argument(
        "--lowpass-hz",
        type=_nonnegative_float,
        default=None,
        help="output low-pass corner; defaults to 17 kHz or 45%% of sample rate, zero disables",
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument(
        "--audio-output",
        type=Path,
        help=".flac or .wav output; FLAC is preferred for long recordings",
    )
    parser.add_argument(
        "--video-output",
        type=Path,
        help="optional .mp4 (AAC) or .mkv (FLAC by default) output",
    )
    parser.add_argument(
        "--resolution",
        default="1920x1080",
        help="black-video resolution WIDTHxHEIGHT (default: 1920x1080)",
    )
    parser.add_argument("--fps", type=_positive_float, default=1.0)
    parser.add_argument("--video-codec", default="libx264")
    parser.add_argument(
        "--audio-codec",
        default=None,
        help="video audio codec; defaults to AAC for MP4 and FLAC for MKV",
    )
    parser.add_argument(
        "--keep-audio",
        action="store_true",
        help="keep the audio master; without --audio-output use the video stem with .flac",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--quiet", action="store_true", help="suppress generation progress")
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def _check_output_paths(args: argparse.Namespace) -> None:
    paths = [path for path in (args.audio_output, args.video_output) if path is not None]
    if len(paths) != len({path.resolve() for path in paths}):
        raise ConfigurationError("audio and video outputs must be different files")
    if not args.force:
        existing = [str(path) for path in paths if path.exists()]
        if existing:
            joined = ", ".join(existing)
            raise ConfigurationError(
                f"output already exists: {joined}; pass --force to overwrite it"
            )


def _run(args: argparse.Namespace) -> int:
    if args.audio_output is None and args.video_output is None:
        raise ConfigurationError("provide --audio-output, --video-output, or both")
    if args.keep_audio and args.audio_output is None and args.video_output is not None:
        args.audio_output = args.video_output.with_suffix(".flac")

    power_ratios = parse_mix(args.mix)
    output_highpass_hz, output_lowpass_hz = _resolve_output_cutoffs(
        args.sample_rate,
        args.highpass_hz,
        args.lowpass_hz,
    )
    config = AudioConfig(
        sample_rate=args.sample_rate,
        channels=args.channels,
        chunk_seconds=args.chunk_seconds,
        target_rms_db=args.target_rms_db,
        peak_ceiling=args.peak_ceiling,
        brown_highpass_hz=args.brown_highpass_hz,
        output_highpass_hz=output_highpass_hz,
        output_lowpass_hz=output_lowpass_hz,
    )
    duration = calculate_duration(
        hours=args.hours,
        minutes=args.minutes,
        seconds=args.seconds,
        sample_rate=config.sample_rate,
    )
    resolution = parse_resolution(args.resolution)
    _check_output_paths(args)

    if args.audio_output is not None:
        validate_audio_output(args.audio_output, duration.frames, config.channels)

    # Fail before generating hours of audio if the requested video dependency
    # is unavailable or the video path is malformed.
    if args.video_output is not None:
        require_ffmpeg()
        if args.video_output.suffix.lower() not in {".mp4", ".mkv"}:
            raise VideoError("video output must use a .mp4 or .mkv extension")

    temporary_directory: TemporaryDirectory | None = None
    if args.audio_output is not None:
        audio_path = args.audio_output
    else:
        temporary_directory = TemporaryDirectory(prefix="noisy-")
        audio_path = Path(temporary_directory.name) / "generated_audio.flac"

    try:
        stats = generate_audio(
            power_ratios=power_ratios,
            duration=duration,
            config=config,
            output_path=str(audio_path),
            seed=args.seed,
            quiet=args.quiet,
        )
        if args.video_output is not None:
            print(f"Muxing pure-black video into {args.video_output}...", file=sys.stderr)
            mux_black_video(
                audio_path,
                args.video_output,
                duration_seconds=stats.seconds,
                resolution=resolution,
                fps=args.fps,
                video_codec=args.video_codec,
                audio_codec=args.audio_codec,
                overwrite=args.force,
            )
        if args.audio_output is not None:
            print(
                f"Wrote {stats.frames} frames ({stats.seconds:.3f}s) to {args.audio_output}",
                file=sys.stderr,
            )
        if args.video_output is not None:
            print(f"Wrote black video to {args.video_output}", file=sys.stderr)
        return 0
    finally:
        if temporary_directory is not None:
            temporary_directory.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130
    except (AudioError, ConfigurationError, MixError, VideoError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


__all__ = ["build_parser", "main"]
