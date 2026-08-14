"""Streaming audio output and fixed-gain mastering."""

from __future__ import annotations

from pathlib import Path
import math
from typing import Any

import numpy as np

try:  # soundfile is a declared runtime dependency
    import soundfile as soundfile_module
except ImportError:  # pragma: no cover - exercised only before installation
    soundfile_module = None


class AudioError(ValueError):
    """Raised when an audio path or audio block cannot be written safely."""


RIFF_FILE_SIZE_LIMIT = (2**32) - 1
PCM_24_BYTES_PER_SAMPLE = 3


def audio_format_for_path(path: str | Path) -> str:
    """Return the supported libsndfile container for a path suffix."""

    suffix = Path(path).suffix.lower()
    if suffix == ".flac":
        return "FLAC"
    if suffix == ".wav":
        return "WAV"
    raise AudioError(
        f"unsupported audio output {path!s}; use a .flac or .wav filename"
    )


def estimate_pcm24_bytes(frames: int, channels: int, *, header_bytes: int = 44) -> int:
    """Estimate an uncompressed PCM-24 WAV size before generation starts."""

    if frames < 0 or channels <= 0:
        raise AudioError("frames and channels must be positive for size estimation")
    return int(header_bytes + frames * channels * PCM_24_BYTES_PER_SAMPLE)


def validate_audio_output(path: str | Path, frames: int, channels: int) -> str:
    """Validate the format and reject oversized classic RIFF WAV requests."""

    output_format = audio_format_for_path(path)
    if output_format == "WAV":
        estimated_size = estimate_pcm24_bytes(frames, channels)
        if estimated_size > RIFF_FILE_SIZE_LIMIT:
            size_gib = estimated_size / (1024**3)
            raise AudioError(
                f"classic RIFF WAV would be approximately {size_gib:.2f} GiB and "
                "exceed the 4 GiB RIFF limit; choose FLAC for this long recording "
                "(RF64 is not enabled by this build)"
            )
    return output_format


class StreamingAudioWriter:
    """A thin context-managed wrapper around ``soundfile.SoundFile``."""

    def __init__(
        self,
        path: str | Path,
        *,
        sample_rate: int,
        channels: int,
        total_frames: int | None = None,
    ) -> None:
        if soundfile_module is None:
            raise AudioError(
                "the soundfile package is required for audio output; install dependencies first"
            )
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.channels = channels
        if total_frames is not None:
            self.format = validate_audio_output(self.path, total_frames, channels)
        else:
            self.format = audio_format_for_path(self.path)
        self.subtype = "PCM_24"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._file = soundfile_module.SoundFile(
                str(self.path),
                mode="w",
                samplerate=sample_rate,
                channels=channels,
                format=self.format,
                subtype=self.subtype,
            )
        except Exception as exc:  # libsndfile has several backend-specific exception types
            raise AudioError(f"could not open audio output {self.path}: {exc}") from exc
        self._closed = False

    def write(self, samples: np.ndarray) -> None:
        if self._closed:
            raise AudioError("cannot write after the audio writer is closed")
        array = np.asarray(samples)
        if array.ndim != 2 or array.shape[1] != self.channels:
            raise AudioError(
                f"audio block must have shape (frames, {self.channels}); got {array.shape}"
            )
        if not np.isfinite(array).all():
            raise AudioError("audio block contains NaN or infinite samples")
        try:
            self._file.write(array)
        except Exception as exc:
            raise AudioError(f"could not write audio output {self.path}: {exc}") from exc

    def close(self) -> None:
        if not self._closed:
            try:
                self._file.close()
            except Exception as exc:
                raise AudioError(f"could not finalize audio output {self.path}: {exc}") from exc
            finally:
                self._closed = True

    def __enter__(self) -> "StreamingAudioWriter":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close()


def apply_master_gain(
    samples: np.ndarray,
    *,
    target_rms_linear: float,
    expected_mix_rms: float = 1.0,
    peak_ceiling: float = 0.98,
) -> np.ndarray:
    """Apply one fixed master gain and a gentle ceiling limiter.

    The gain is derived from the configured target and the mix's expected
    unit-source RMS.  It is intentionally not recomputed from each block.  A
    smooth saturating curve only affects samples above the soft-knee threshold
    and maps arbitrarily large random peaks below the configured ceiling.
    """

    if not math.isfinite(target_rms_linear) or target_rms_linear < 0:
        raise AudioError("target RMS must be finite and non-negative")
    if not math.isfinite(expected_mix_rms) or expected_mix_rms <= 0:
        raise AudioError("expected mix RMS must be finite and greater than zero")
    if not math.isfinite(peak_ceiling) or not 0 < peak_ceiling <= 1:
        raise AudioError("peak ceiling must be finite and in the range (0, 1]")

    array = np.asarray(samples, dtype=np.float64)
    if array.ndim != 2:
        raise AudioError("master input must have shape (frames, channels)")
    if not np.isfinite(array).all():
        raise AudioError("master input contains NaN or infinite samples")

    scaled = array * (target_rms_linear / expected_mix_rms)
    threshold = peak_ceiling * 0.90
    magnitude = np.abs(scaled)
    over_threshold = magnitude > threshold
    if np.any(over_threshold):
        excess = magnitude[over_threshold] - threshold
        softened = threshold + (peak_ceiling - threshold) * np.tanh(
            excess / (peak_ceiling - threshold)
        )
        scaled[over_threshold] = np.copysign(softened, scaled[over_threshold])

    # tanh is bounded, but keep a defensive assertion against future changes
    # or floating-point surprises that could otherwise produce a clipped file.
    if np.any(np.abs(scaled) > peak_ceiling + 1e-12):
        raise AudioError("peak protection failed to enforce the configured ceiling")
    return scaled.astype(np.float32, copy=False)


__all__ = [
    "AudioError",
    "RIFF_FILE_SIZE_LIMIT",
    "StreamingAudioWriter",
    "apply_master_gain",
    "audio_format_for_path",
    "estimate_pcm24_bytes",
    "validate_audio_output",
]
