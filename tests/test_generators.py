from __future__ import annotations

import numpy as np
import pytest

from noisy.generators import SUPPORTED_COLORS, make_generator
from noisy.mixer import NoiseMixer


@pytest.mark.parametrize("color", SUPPORTED_COLORS)
def test_seeded_generators_are_deterministic_and_finite(color: str) -> None:
    first = make_generator(color, 8_000, 1, seed=123, calibration_seconds=0.1)
    second = make_generator(color, 8_000, 1, seed=123, calibration_seconds=0.1)
    first_samples = first.generate(4_096)
    second_samples = second.generate(4_096)
    np.testing.assert_array_equal(first_samples, second_samples)
    assert first_samples.shape == (4_096, 1)
    assert first_samples.dtype == np.float32
    assert np.isfinite(first_samples).all()


@pytest.mark.parametrize("color", SUPPORTED_COLORS)
def test_generator_state_is_continuous_across_chunks(color: str) -> None:
    whole = make_generator(color, 8_000, 2, seed=456, calibration_seconds=0.1)
    chunked = make_generator(color, 8_000, 2, seed=456, calibration_seconds=0.1)
    expected = whole.generate(6_000)
    actual = np.vstack((chunked.generate(1_111), chunked.generate(4_889)))
    np.testing.assert_allclose(actual, expected, rtol=0, atol=2e-6)


def test_mixer_is_stereo_with_independent_seeded_channels() -> None:
    mixer = NoiseMixer({"pink": 0.8, "blue": 0.2}, 8_000, 2, seed=99, calibration_seconds=0.1)
    block = mixer.generate(4_000)
    assert block.shape == (4_000, 2)
    assert np.isfinite(block).all()
    assert not np.array_equal(block[:, 0], block[:, 1])


def test_mixer_expected_rms_is_unit_after_ratio_normalization() -> None:
    mixer = NoiseMixer({"brown": 60, "pink": 30, "white": 10}, 8_000, 1, seed=1, calibration_seconds=0.1)
    assert mixer.expected_rms == pytest.approx(1.0)
