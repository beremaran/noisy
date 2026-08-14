"""Small stateful DSP building blocks used by the noise generators.

SciPy is used for the production IIR/SOS implementations.  The compact
NumPy fallbacks keep the package importable for environments that are still
installing dependencies and are useful for basic smoke tests; the declared
project dependencies install SciPy for normal operation.
"""

from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Tuple

import numpy as np

try:  # pragma: no cover - exercised implicitly when SciPy is installed
    from scipy import signal as scipy_signal
except ImportError:  # pragma: no cover - fallback is covered instead
    scipy_signal = None


def fractional_difference_coefficients(order: float, taps: int = 257) -> np.ndarray:
    """Return a finite causal approximation to ``(1 - z^-1)**order``.

    A fractional difference is a useful streaming approximation here:
    ``order=0.5`` gives blue-noise-like +3 dB/octave power slope and
    ``order=1`` gives violet-noise-like +6 dB/octave slope.  Truncating the
    long-tailed impulse response makes the operation finite-memory and keeps
    the generator safe for multi-hour streams.
    """

    if not math.isfinite(order) or order < 0:
        raise ValueError("fractional difference order must be finite and non-negative")
    if not isinstance(taps, int) or taps < 2:
        raise ValueError("fractional difference tap count must be an integer of at least 2")

    coefficients = np.empty(taps, dtype=np.float64)
    coefficients[0] = 1.0
    for index in range(1, taps):
        coefficients[index] = coefficients[index - 1] * (index - 1 - order) / index
    return coefficients


def lfilter_chunk(
    numerator: np.ndarray | list[float],
    denominator: np.ndarray | list[float],
    samples: np.ndarray,
    state: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Filter a ``(frames, channels)`` block while retaining state."""

    b = np.asarray(numerator, dtype=np.float64)
    a = np.asarray(denominator, dtype=np.float64)
    x = np.asarray(samples, dtype=np.float64)
    if x.ndim != 2:
        raise ValueError("streaming filter input must have shape (frames, channels)")
    if a.size == 0 or b.size == 0 or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("filter coefficients must be finite and non-empty")
    if a[0] == 0:
        raise ValueError("filter denominator must have a non-zero leading coefficient")

    if scipy_signal is not None:
        return scipy_signal.lfilter(b, a, x, axis=0, zi=state)

    # Direct-form-II transposed fallback.  Normal operation uses SciPy's
    # optimized implementation, but this keeps small installs functional.
    b = b / a[0]
    a = a / a[0]
    order = max(b.size, a.size) - 1
    padded_b = np.pad(b, (0, order + 1 - b.size))
    padded_a = np.pad(a, (0, order + 1 - a.size))
    if state.shape != (order, x.shape[1]):
        raise ValueError(f"filter state must have shape {(order, x.shape[1])}")

    output = np.empty_like(x, dtype=np.float64)
    zi = np.array(state, dtype=np.float64, copy=True)
    for frame_index in range(x.shape[0]):
        sample = x[frame_index]
        value = padded_b[0] * sample
        if order:
            value = value + zi[0]
            for state_index in range(order - 1):
                zi[state_index] = (
                    padded_b[state_index + 1] * sample
                    + zi[state_index + 1]
                    - padded_a[state_index + 1] * value
                )
            zi[-1] = padded_b[order] * sample - padded_a[order] * value
        output[frame_index] = value
    return output, zi


class StreamingFIR:
    """A causal finite impulse response filter with chunk-persistent history."""

    def __init__(self, coefficients: np.ndarray, channels: int) -> None:
        coefficients = np.asarray(coefficients, dtype=np.float64)
        if coefficients.ndim != 1 or coefficients.size == 0:
            raise ValueError("FIR coefficients must be a non-empty one-dimensional array")
        if channels <= 0:
            raise ValueError("channel count must be positive")
        self.coefficients = coefficients
        self.state = np.zeros((max(0, coefficients.size - 1), channels), dtype=np.float64)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.state.shape[1]:
            raise ValueError("FIR input must have shape (frames, configured channels)")
        if x.shape[0] == 0:
            return np.empty_like(x)

        if scipy_signal is not None:
            output, self.state = scipy_signal.lfilter(
                self.coefficients,
                [1.0],
                x,
                axis=0,
                zi=self.state,
            )
            return output

        # This path is only a dependency-light fallback.  The history is
        # small (257 samples by default), so a vectorized convolution per
        # channel is still bounded in memory.
        history_length = self.state.shape[0]
        extended = np.vstack((self.state, x))
        output = np.empty_like(x, dtype=np.float64)
        for channel in range(x.shape[1]):
            convolution = np.convolve(
                extended[:, channel], self.coefficients, mode="full"
            )
            output[:, channel] = convolution[history_length : history_length + x.shape[0]]
        if history_length:
            self.state = extended[-history_length:]
        return output


class StreamingHighPass:
    """A stable high-pass filter retaining state across audio blocks."""

    def __init__(self, sample_rate: int, cutoff_hz: float, channels: int) -> None:
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral) or sample_rate <= 0:
            raise ValueError("sample rate must be a positive integer")
        if isinstance(channels, bool) or not isinstance(channels, Integral) or channels <= 0:
            raise ValueError("channel count must be a positive integer")
        if (
            isinstance(cutoff_hz, bool)
            or not isinstance(cutoff_hz, Real)
            or not math.isfinite(cutoff_hz)
            or not 0 < cutoff_hz < sample_rate / 2
        ):
            raise ValueError("high-pass cutoff must be finite, positive, and below Nyquist")
        self.channels = channels
        self.cutoff_hz = cutoff_hz
        self.sample_rate = sample_rate

        if scipy_signal is not None:
            self._sos = scipy_signal.butter(
                2,
                cutoff_hz,
                btype="highpass",
                fs=sample_rate,
                output="sos",
            )
            self._sos_state = np.zeros(
                (self._sos.shape[0], 2, channels), dtype=np.float64
            )
        else:
            self._sos = None
            self._sos_state = None
            # One-pole fallback: y[n] = a * (y[n-1] + x[n] - x[n-1]).
            self._alpha = math.exp(-2.0 * math.pi * cutoff_hz / sample_rate)
            self._previous_input = np.zeros(channels, dtype=np.float64)
            self._previous_output = np.zeros(channels, dtype=np.float64)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.channels:
            raise ValueError("high-pass input must have shape (frames, configured channels)")
        if x.shape[0] == 0:
            return np.empty_like(x)

        if scipy_signal is not None:
            output, self._sos_state = scipy_signal.sosfilt(
                self._sos,
                x,
                axis=0,
                zi=self._sos_state,
            )
            return output

        output = np.empty_like(x, dtype=np.float64)
        previous_input = self._previous_input
        previous_output = self._previous_output
        for frame_index, sample in enumerate(x):
            current = self._alpha * (previous_output + sample - previous_input)
            output[frame_index] = current
            previous_input = sample
            previous_output = current
        self._previous_input = previous_input
        self._previous_output = previous_output
        return output


class StreamingBandLimit:
    """A causal high/low-pass cascade retaining state across audio blocks."""

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        *,
        highpass_hz: float | None = None,
        lowpass_hz: float | None = None,
        order: int = 2,
    ) -> None:
        for value, name in ((sample_rate, "sample rate"), (channels, "channel count"), (order, "filter order")):
            if isinstance(value, bool) or not isinstance(value, Integral) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")

        nyquist = sample_rate / 2.0
        for cutoff, name in ((highpass_hz, "high-pass"), (lowpass_hz, "low-pass")):
            if cutoff is not None and (
                isinstance(cutoff, bool)
                or not isinstance(cutoff, Real)
                or not math.isfinite(cutoff)
                or not 0 < cutoff < nyquist
            ):
                raise ValueError(f"{name} cutoff must be finite, positive, and below Nyquist")
        if highpass_hz is not None and lowpass_hz is not None and highpass_hz >= lowpass_hz:
            raise ValueError("high-pass cutoff must be below low-pass cutoff")
        if (highpass_hz is not None or lowpass_hz is not None) and scipy_signal is None:
            raise RuntimeError("SciPy is required for StreamingBandLimit filtering")

        self.channels = channels
        self._filters: tuple[StreamingSOS, ...]
        filters = []
        for cutoff, btype in ((highpass_hz, "highpass"), (lowpass_hz, "lowpass")):
            if cutoff is not None:
                sos = scipy_signal.butter(order, cutoff, btype=btype, fs=sample_rate, output="sos")
                filters.append(StreamingSOS(sos, channels))
        self._filters = tuple(filters)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.channels:
            raise ValueError("band-limit input must have shape (frames, configured channels)")
        for filter_ in self._filters:
            x = filter_.process(x)
        return x


def fractional_derivative_sos(
    sample_rate: int,
    order: float = 0.5,
    *,
    low_hz: float = 20.0,
    high_hz: float | None = None,
    sections: int = 3,
) -> np.ndarray | None:
    """Design a stable Oustaloup-style fractional differentiator.

    The returned SOS cascade approximates ``s**order`` between ``low_hz`` and
    ``high_hz``.  It is deliberately finite-order and causal, so it can be
    processed block by block.  ``None`` is returned only when SciPy is not
    available; callers can then choose a simpler dependency-free fallback.
    """

    if isinstance(sample_rate, bool) or not isinstance(sample_rate, Integral) or sample_rate <= 0:
        raise ValueError("sample rate must be a positive integer")
    if isinstance(order, bool) or not isinstance(order, Real) or not math.isfinite(order) or not 0 <= order <= 1:
        raise ValueError("fractional derivative order must be between zero and one")
    if isinstance(sections, bool) or not isinstance(sections, Integral) or sections < 1:
        raise ValueError("fractional derivative section count must be a positive integer")

    nyquist = sample_rate / 2.0
    if (
        isinstance(low_hz, bool)
        or not isinstance(low_hz, Real)
        or not math.isfinite(low_hz)
        or not 0 < low_hz < nyquist
    ):
        raise ValueError("fractional derivative low_hz must be finite, positive, and below Nyquist")
    if high_hz is None:
        high_hz = 0.45 * sample_rate
    elif (
        isinstance(high_hz, bool)
        or not isinstance(high_hz, Real)
        or not math.isfinite(high_hz)
        or not 0 < high_hz < nyquist
    ):
        raise ValueError("fractional derivative high_hz must be finite, positive, and below Nyquist")
    if not low_hz < high_hz < nyquist:
        raise ValueError("fractional derivative frequency range must fit below Nyquist")
    if scipy_signal is None:
        return None

    # Prewarp the digital design limits before applying the bilinear transform.
    omega_low = 2.0 * sample_rate * math.tan(math.pi * low_hz / sample_rate)
    omega_high = 2.0 * sample_rate * math.tan(math.pi * high_hz / sample_rate)
    count = 2 * sections + 1
    indices = np.arange(-sections, sections + 1, dtype=np.float64)
    ratio = omega_high / omega_low

    zero_exponents = (indices + sections + (1.0 - order) / 2.0) / count
    pole_exponents = (indices + sections + (1.0 + order) / 2.0) / count
    zero_frequencies = omega_low * ratio**zero_exponents
    pole_frequencies = omega_low * ratio**pole_exponents

    analog_zeros = -zero_frequencies
    analog_poles = -pole_frequencies
    analog_gain = omega_high**order
    digital_zeros, digital_poles, digital_gain = scipy_signal.bilinear_zpk(
        analog_zeros,
        analog_poles,
        analog_gain,
        fs=sample_rate,
    )
    sos = scipy_signal.zpk2sos(digital_zeros, digital_poles, digital_gain)
    if not np.isfinite(sos).all():
        raise ValueError("fractional derivative design produced non-finite coefficients")
    return np.asarray(sos, dtype=np.float64)


class StreamingSOS:
    """A second-order-section cascade with persistent per-channel state."""

    def __init__(self, sos: np.ndarray, channels: int) -> None:
        coefficients = np.asarray(sos, dtype=np.float64)
        if coefficients.ndim != 2 or coefficients.shape[1] != 6 or coefficients.shape[0] == 0:
            raise ValueError("SOS coefficients must have shape (sections, 6)")
        if not np.isfinite(coefficients).all():
            raise ValueError("SOS coefficients must be finite")
        self.sos = coefficients
        self.channels = channels
        self.state = np.zeros((coefficients.shape[0], 2, channels), dtype=np.float64)

    def process(self, samples: np.ndarray) -> np.ndarray:
        x = np.asarray(samples, dtype=np.float64)
        if x.ndim != 2 or x.shape[1] != self.channels:
            raise ValueError("SOS input must have shape (frames, configured channels)")
        if x.shape[0] == 0:
            return np.empty_like(x)

        if scipy_signal is not None:
            output, self.state = scipy_signal.sosfilt(
                self.sos,
                x,
                axis=0,
                zi=self.state,
            )
            return output

        output = x
        for section_index, section in enumerate(self.sos):
            b = section[:3]
            a = section[3:]
            output, self.state[section_index] = lfilter_chunk(
                b,
                a,
                output,
                self.state[section_index],
            )
        return output
