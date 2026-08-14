"""End-to-end streaming audio generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
import sys
import time
from typing import TextIO

import numpy as np

from .audio import AudioError, StreamingAudioWriter, apply_master_gain
from .config import AudioConfig, Duration
from .dsp import StreamingBandLimit
from .mixer import NoiseMixer


@dataclass(frozen=True)
class GenerationStats:
    """Small summary returned after an audio stream has been finalized."""

    frames: int
    seconds: float
    output_path: str


class ProgressReporter:
    """Low-overhead terminal progress for long-running generation."""

    def __init__(self, total_frames: int, sample_rate: int, *, quiet: bool, stream: TextIO) -> None:
        self.total_frames = total_frames
        self.sample_rate = sample_rate
        self.quiet = quiet
        self.stream = stream
        self.started = time.monotonic()
        self.last_update = 0.0
        self.last_frames = 0

    def update(self, frames: int, *, force: bool = False) -> None:
        if self.quiet:
            return
        if self.last_frames >= self.total_frames and frames >= self.total_frames:
            return
        now = time.monotonic()
        if not force and frames < self.total_frames and now - self.last_update < 0.5:
            return
        elapsed = max(now - self.started, 1e-9)
        fraction = frames / self.total_frames
        rate = frames / elapsed
        remaining = max(self.total_frames - frames, 0)
        eta = remaining / rate if rate > 0 else 0.0
        percent = min(100.0, 100.0 * fraction)
        message = (
            f"\rGenerating audio: {percent:6.2f}% | "
            f"{frames / self.sample_rate:.1f}s / {self.total_frames / self.sample_rate:.1f}s | "
            f"ETA {eta:.0f}s"
        )
        self.stream.write(message)
        self.stream.flush()
        self.last_update = now
        self.last_frames = frames
        if frames >= self.total_frames:
            self.stream.write("\n")


def _calibrate_output_filter(
    mixer: NoiseMixer,
    output_filter: StreamingBandLimit,
    calibration_frames: int,
) -> float:
    """Warm the causal filter and return one fixed RMS for master gain.

    The calibration block is discarded so filter startup transients cannot be
    heard.  Measure only its settled tail, while still sending the complete
    block through the filter so its state is fully warmed.  That RMS is
    retained for the whole output stream rather than recalculated at chunk
    boundaries.
    """

    calibration = output_filter.process(mixer.generate(calibration_frames))
    # Ignore the initial half of the pre-roll when estimating the settled band
    # level, but keep it in the filter state before output begins.
    settled_start = min(calibration.shape[0] - 1, calibration.shape[0] // 2)
    settled_calibration = calibration[settled_start:]
    expected_rms = float(np.sqrt(np.mean(np.square(settled_calibration))))
    if not np.isfinite(expected_rms) or expected_rms <= 0:
        raise AudioError("filtered calibration produced an unusable RMS reference")
    return expected_rms


def generate_audio(
    *,
    power_ratios: dict[str, float],
    duration: Duration,
    config: AudioConfig,
    output_path: str,
    seed: int | None = None,
    quiet: bool = False,
    progress_stream: TextIO = sys.stderr,
) -> GenerationStats:
    """Generate and finalize a streaming audio file."""

    mixer = NoiseMixer(
        power_ratios,
        config.sample_rate,
        config.channels,
        seed=seed,
        calibration_seconds=config.calibration_seconds,
        brown_highpass_hz=config.brown_highpass_hz,
    )
    progress = ProgressReporter(
        duration.frames,
        config.sample_rate,
        quiet=quiet,
        stream=progress_stream,
    )
    output_filter = None
    expected_mix_rms = mixer.expected_rms
    if config.output_highpass_hz is not None or config.output_lowpass_hz is not None:
        output_filter = StreamingBandLimit(
            config.sample_rate,
            config.channels,
            highpass_hz=config.output_highpass_hz,
            lowpass_hz=config.output_lowpass_hz,
        )
        calibration_frames = max(
            4_096, int(round(config.sample_rate * config.calibration_seconds))
        )
        expected_mix_rms = _calibrate_output_filter(mixer, output_filter, calibration_frames)
    remaining = duration.frames
    generated = 0
    with StreamingAudioWriter(
        output_path,
        sample_rate=config.sample_rate,
        channels=config.channels,
        total_frames=duration.frames,
    ) as writer:
        while remaining:
            frames = min(config.chunk_frames, remaining)
            mixed = mixer.generate(frames)
            if output_filter is not None:
                mixed = output_filter.process(mixed)
            mastered = apply_master_gain(
                mixed,
                target_rms_linear=config.target_rms_linear,
                expected_mix_rms=expected_mix_rms,
                peak_ceiling=config.peak_ceiling,
            )
            writer.write(mastered)
            generated += frames
            remaining -= frames
            progress.update(generated)

    progress.update(generated, force=True)
    return GenerationStats(
        frames=generated,
        seconds=generated / config.sample_rate,
        output_path=output_path,
    )


__all__ = ["GenerationStats", "ProgressReporter", "generate_audio"]
