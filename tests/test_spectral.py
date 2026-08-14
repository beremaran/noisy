from __future__ import annotations

import numpy as np
import pytest

scipy_signal = pytest.importorskip("scipy.signal")

from noisy.generators import make_generator


def _slope_db_per_octave(samples: np.ndarray, sample_rate: int) -> float:
    frequencies, power = scipy_signal.welch(
        samples,
        fs=sample_rate,
        nperseg=16_384,
        detrend="constant",
    )
    usable = (frequencies >= 80.0) & (frequencies <= 1_000.0) & (power > 0)
    return float(np.polyfit(np.log2(frequencies[usable]), 10.0 * np.log10(power[usable]), 1)[0])


@pytest.mark.parametrize(
    ("color", "expected"),
    [("white", 0.0), ("pink", -3.0), ("brown", -6.0), ("blue", 3.0), ("violet", 6.0)],
)
def test_streaming_colors_have_expected_approximate_spectral_slopes(color: str, expected: float) -> None:
    sample_rate = 8_000
    generator = make_generator(color, sample_rate, 1, seed=2024, calibration_seconds=0.5)
    samples = generator.generate(262_144)[:, 0]
    measured = _slope_db_per_octave(samples, sample_rate)
    assert measured == pytest.approx(expected, abs=1.25)
