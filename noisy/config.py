"""Validated configuration and duration helpers."""

from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real


class ConfigurationError(ValueError):
    """Raised when a CLI or library configuration is not usable."""


def _require_finite_real(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ConfigurationError(f"{name} must be a finite numeric value")


@dataclass(frozen=True)
class Duration:
    """A requested duration represented in seconds and exact sample frames."""

    seconds: float
    frames: int


@dataclass(frozen=True)
class AudioConfig:
    """Parameters shared by the streaming generator and audio writer."""

    sample_rate: int = 48_000
    channels: int = 1
    chunk_seconds: float = 5.0
    target_rms_db: float = -18.0
    peak_ceiling: float = 0.98
    brown_highpass_hz: float = 12.0
    calibration_seconds: float = 4.0
    output_highpass_hz: float | None = None
    output_lowpass_hz: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.sample_rate, int) or isinstance(self.sample_rate, bool):
            raise ConfigurationError("sample rate must be an integer")
        if self.sample_rate <= 0:
            raise ConfigurationError("sample rate must be greater than zero")
        if isinstance(self.channels, bool) or self.channels not in (1, 2):
            raise ConfigurationError("channels must be 1 (mono) or 2 (stereo)")
        _require_finite_real("chunk duration", self.chunk_seconds)
        if self.chunk_seconds <= 0:
            raise ConfigurationError("chunk duration must be a finite value greater than zero")
        _require_finite_real("target RMS", self.target_rms_db)
        if self.target_rms_db > 0:
            raise ConfigurationError("target RMS must be finite and at or below 0 dBFS")
        _require_finite_real("peak ceiling", self.peak_ceiling)
        if not 0 < self.peak_ceiling <= 1:
            raise ConfigurationError("peak ceiling must be finite and in the range (0, 1]")
        _require_finite_real("brown high-pass frequency", self.brown_highpass_hz)
        if self.brown_highpass_hz <= 0:
            raise ConfigurationError("brown high-pass frequency must be greater than zero")
        if self.brown_highpass_hz >= self.sample_rate / 2:
            raise ConfigurationError("brown high-pass frequency must be below the Nyquist frequency")
        nyquist = self.sample_rate / 2
        for name, cutoff in (
            ("output high-pass frequency", self.output_highpass_hz),
            ("output low-pass frequency", self.output_lowpass_hz),
        ):
            if cutoff is None:
                continue
            _require_finite_real(name, cutoff)
            if not 0 < cutoff < nyquist:
                raise ConfigurationError(f"{name} must be finite, greater than zero, and below Nyquist")
        if (
            self.output_highpass_hz is not None
            and self.output_lowpass_hz is not None
            and self.output_highpass_hz >= self.output_lowpass_hz
        ):
            raise ConfigurationError("output high-pass frequency must be below the output low-pass frequency")
        _require_finite_real("calibration duration", self.calibration_seconds)
        if self.calibration_seconds <= 0:
            raise ConfigurationError("calibration duration must be greater than zero")

    @property
    def chunk_frames(self) -> int:
        """Number of frames in a normal generation block."""

        return max(1, int(round(self.sample_rate * self.chunk_seconds)))

    @property
    def target_rms_linear(self) -> float:
        """Target RMS expressed as a linear full-scale amplitude."""

        return 10.0 ** (self.target_rms_db / 20.0)


def calculate_duration(
    *,
    hours: float = 0.0,
    minutes: float = 0.0,
    seconds: float = 0.0,
    sample_rate: int = 48_000,
) -> Duration:
    """Combine duration flags and convert them to a sample-accurate request.

    The individual components may be fractional, which is useful for short
    smoke tests.  Frames are rounded once, after all components are combined,
    so generation never has to accumulate floating-point duration errors.
    """

    if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
        raise ConfigurationError("sample rate must be a positive integer")

    values = {"hours": hours, "minutes": minutes, "seconds": seconds}
    for name, value in values.items():
        _require_finite_real(name, value)
        if value < 0:
            raise ConfigurationError(f"{name} must be a finite value greater than or equal to zero")

    total_seconds = hours * 3600.0 + minutes * 60.0 + seconds
    if not math.isfinite(total_seconds) or total_seconds <= 0:
        raise ConfigurationError("duration must be greater than zero")

    frames = int(round(total_seconds * sample_rate))
    if frames <= 0:
        raise ConfigurationError("duration is shorter than one audio frame at the selected sample rate")

    # NumPy and soundfile use signed platform-sized integers for frame counts.
    if frames > (2**63 - 1):
        raise ConfigurationError("duration is too large to represent as an audio stream")

    return Duration(seconds=frames / sample_rate, frames=frames)
