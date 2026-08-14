"""Stateful streaming colored-noise generators."""

from __future__ import annotations

from abc import ABC, abstractmethod
import math

import numpy as np

from .dsp import (
    StreamingFIR,
    StreamingHighPass,
    StreamingSOS,
    fractional_derivative_sos,
    fractional_difference_coefficients,
    lfilter_chunk,
)


SUPPORTED_COLORS = ("white", "pink", "brown", "blue", "violet")
COLOR_ALIASES = {"red": "brown"}


def canonical_color(name: str) -> str:
    """Normalize a color name and accept ``red`` as a brown-noise alias."""

    normalized = name.strip().lower()
    normalized = COLOR_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_COLORS:
        available = ", ".join((*SUPPORTED_COLORS, "red (alias for brown)"))
        raise ValueError(f"unknown noise color {name!r}; choose from {available}")
    return normalized


def _channel_rngs(seed: int | None, channels: int) -> list[np.random.Generator]:
    if seed is None:
        return [np.random.default_rng() for _ in range(channels)]
    if not isinstance(seed, (int, np.integer)) or isinstance(seed, bool):
        raise ValueError("seed must be an integer")
    sequence = np.random.SeedSequence(int(seed))
    return [np.random.default_rng(child) for child in sequence.spawn(channels)]


class NoiseGenerator(ABC):
    """Common interface for all streaming noise sources.

    Subclasses implement ``_generate_raw`` and may use arbitrary internal DSP
    state.  A short discarded warm-up block establishes a stable per-channel
    RMS reference; all subsequent blocks are scaled by that fixed gain.  No
    output block is normalized independently, so chunk boundaries cannot pump
    the level.
    """

    color: str

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        *,
        seed: int | None = None,
        calibration_seconds: float = 4.0,
    ) -> None:
        if not isinstance(sample_rate, int) or isinstance(sample_rate, bool) or sample_rate <= 0:
            raise ValueError("sample rate must be a positive integer")
        if channels not in (1, 2):
            raise ValueError("channels must be 1 or 2")
        if not math.isfinite(calibration_seconds) or calibration_seconds <= 0:
            raise ValueError("calibration duration must be greater than zero")

        self.sample_rate = sample_rate
        self.channels = channels
        self._rngs = _channel_rngs(seed, channels)

        calibration_frames = max(4_096, int(round(sample_rate * calibration_seconds)))
        calibration = self._generate_raw(calibration_frames)
        if calibration.shape != (calibration_frames, channels):
            raise RuntimeError(
                f"{self.color} generator returned an invalid calibration shape "
                f"{calibration.shape}; expected {(calibration_frames, channels)}"
            )
        calibration_rms = np.sqrt(np.mean(np.square(calibration), axis=0))
        if not np.isfinite(calibration_rms).all() or np.any(calibration_rms <= np.finfo(np.float64).tiny):
            raise RuntimeError(f"{self.color} generator produced an unusable RMS reference")
        self._rms_gain = 1.0 / calibration_rms

    @property
    def rms_gain(self) -> np.ndarray:
        """Fixed per-channel gain used to produce the unit-RMS source."""

        return self._rms_gain.copy()

    def generate(self, frames: int) -> np.ndarray:
        """Generate ``frames`` rows of unit-RMS noise without resetting state."""

        if not isinstance(frames, (int, np.integer)) or isinstance(frames, bool):
            raise ValueError("frame count must be an integer")
        if frames < 0:
            raise ValueError("frame count must not be negative")
        if frames == 0:
            return np.empty((0, self.channels), dtype=np.float32)

        raw = np.asarray(self._generate_raw(int(frames)), dtype=np.float64)
        if raw.shape != (frames, self.channels):
            raise RuntimeError(
                f"{self.color} generator returned shape {raw.shape}; "
                f"expected {(frames, self.channels)}"
            )
        if not np.isfinite(raw).all():
            raise RuntimeError(f"{self.color} generator produced NaN or infinite samples")
        return (raw * self._rms_gain).astype(np.float32, copy=False)

    def _white(self, frames: int) -> np.ndarray:
        output = np.empty((frames, self.channels), dtype=np.float64)
        for channel, rng in enumerate(self._rngs):
            output[:, channel] = rng.standard_normal(frames)
        return output

    @abstractmethod
    def _generate_raw(self, frames: int) -> np.ndarray:
        """Generate an unnormalized block and advance all internal state."""


class WhiteNoiseGenerator(NoiseGenerator):
    color = "white"

    def _generate_raw(self, frames: int) -> np.ndarray:
        return self._white(frames)


class PinkNoiseGenerator(NoiseGenerator):
    """Paul Kellet-style parallel IIR pinking filter.

    The filter is a compact, established approximation to 1/f power noise.
    Each one-pole branch retains its own state and is evaluated chunkwise with
    SciPy's stateful ``lfilter`` implementation (or the package fallback).
    """

    color = "pink"

    _POLES = (0.99886, 0.99332, 0.96900, 0.86650, 0.55000, -0.76160)
    _INPUT_GAINS = (0.0555179, 0.0750759, 0.1538520, 0.3104856, 0.5329522, -0.0168980)
    _DIRECT_GAIN = 0.5362
    _B6_GAIN = 0.115926

    def __init__(self, *args, **kwargs) -> None:
        # ``channels`` is needed before the base class performs its discarded
        # calibration block.
        channels = kwargs.get("channels")
        if channels is None and len(args) > 1:
            channels = args[1]
        if channels is None:
            raise ValueError("channels are required")
        self.channels = channels
        self._states = [np.zeros((1, channels), dtype=np.float64) for _ in self._POLES]
        super().__init__(*args, **kwargs)

    def _generate_raw(self, frames: int) -> np.ndarray:
        white = self._white(frames)
        output = white * self._DIRECT_GAIN
        for index, (pole, input_gain) in enumerate(zip(self._POLES, self._INPUT_GAINS)):
            filtered, self._states[index] = lfilter_chunk(
                [input_gain],
                [1.0, -pole],
                white,
                self._states[index],
            )
            output += filtered
        output += white * self._B6_GAIN
        return output


class BrownNoiseGenerator(NoiseGenerator):
    """Leaky integrated white noise followed by a low-frequency high-pass.

    The small integrator leak keeps the random-walk state bounded over many
    hours.  The configurable high-pass then removes subsonic drift and DC,
    while leaving the intended approximately 1/f² power relationship above
    the cutoff.
    """

    color = "brown"

    def __init__(self, *args, highpass_hz: float = 12.0, **kwargs) -> None:
        sample_rate = kwargs.get("sample_rate")
        if sample_rate is None and args:
            sample_rate = args[0]
        if sample_rate is None:
            raise ValueError("sample rate is required")
        channels = kwargs.get("channels")
        if channels is None and len(args) > 1:
            channels = args[1]
        if channels is None:
            raise ValueError("channels are required")
        if not math.isfinite(highpass_hz) or not 0 < highpass_hz < sample_rate / 2:
            raise ValueError("brown high-pass frequency must be between zero and Nyquist")

        self.highpass_hz = highpass_hz
        # A separate, lower leak bounds the integrator without moving the
        # user-visible high-pass corner.  At the default settings this is a
        # 1 Hz leak and a 12 Hz output high-pass.
        leak_hz = min(1.0, max(0.1, highpass_hz / 10.0))
        self._integrator_pole = math.exp(-2.0 * math.pi * leak_hz / sample_rate)
        self._integrator_state = np.zeros((1, channels), dtype=np.float64)
        self._highpass = StreamingHighPass(sample_rate, highpass_hz, channels)
        super().__init__(*args, **kwargs)

    def _generate_raw(self, frames: int) -> np.ndarray:
        white = self._white(frames)
        integrated, self._integrator_state = lfilter_chunk(
            [1.0],
            [1.0, -self._integrator_pole],
            white,
            self._integrator_state,
        )
        return self._highpass.process(integrated)


class BlueNoiseGenerator(NoiseGenerator):
    """Stable half-order differentiator producing blue-noise-like output."""

    color = "blue"

    def __init__(self, *args, **kwargs) -> None:
        sample_rate = kwargs.get("sample_rate")
        if sample_rate is None and args:
            sample_rate = args[0]
        channels = kwargs.get("channels")
        if channels is None and len(args) > 1:
            channels = args[1]
        if sample_rate is None or channels is None:
            raise ValueError("sample rate and channels are required")

        sos = fractional_derivative_sos(sample_rate, order=0.5)
        if sos is not None:
            self._filter = StreamingSOS(sos, channels)
        else:  # pragma: no cover - only used without declared SciPy dependency
            self._filter = StreamingFIR(fractional_difference_coefficients(0.5), channels)
        super().__init__(*args, **kwargs)

    def _generate_raw(self, frames: int) -> np.ndarray:
        return self._filter.process(self._white(frames))


class VioletNoiseGenerator(NoiseGenerator):
    """First difference of white noise, a streaming violet-noise model."""

    color = "violet"

    def __init__(self, *args, **kwargs) -> None:
        sample_rate = kwargs.get("sample_rate")
        if sample_rate is None and args:
            sample_rate = args[0]
        channels = kwargs.get("channels")
        if channels is None and len(args) > 1:
            channels = args[1]
        if sample_rate is None or channels is None:
            raise ValueError("sample rate and channels are required")
        # The finite difference is exactly a causal first-order differentiator
        # and needs only one sample of state.
        self._filter = StreamingFIR(fractional_difference_coefficients(1.0, taps=2), channels)
        super().__init__(*args, **kwargs)

    def _generate_raw(self, frames: int) -> np.ndarray:
        return self._filter.process(self._white(frames))


_GENERATOR_TYPES = {
    "white": WhiteNoiseGenerator,
    "pink": PinkNoiseGenerator,
    "brown": BrownNoiseGenerator,
    "blue": BlueNoiseGenerator,
    "violet": VioletNoiseGenerator,
}


def make_generator(
    color: str,
    sample_rate: int,
    channels: int,
    *,
    seed: int | None = None,
    calibration_seconds: float = 4.0,
    brown_highpass_hz: float = 12.0,
) -> NoiseGenerator:
    """Construct a generator from a validated public color name."""

    canonical = canonical_color(color)
    generator_type = _GENERATOR_TYPES[canonical]
    common = {
        "sample_rate": sample_rate,
        "channels": channels,
        "seed": seed,
        "calibration_seconds": calibration_seconds,
    }
    if canonical == "brown":
        common["highpass_hz"] = brown_highpass_hz
    return generator_type(**common)


__all__ = [
    "SUPPORTED_COLORS",
    "COLOR_ALIASES",
    "NoiseGenerator",
    "WhiteNoiseGenerator",
    "PinkNoiseGenerator",
    "BrownNoiseGenerator",
    "BlueNoiseGenerator",
    "VioletNoiseGenerator",
    "canonical_color",
    "make_generator",
]
