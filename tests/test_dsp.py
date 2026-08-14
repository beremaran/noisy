from __future__ import annotations

import numpy as np
import pytest

import noisy.dsp as dsp
from noisy.dsp import StreamingBandLimit, StreamingHighPass, fractional_derivative_sos


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 0},
        {"sample_rate": True},
        {"channels": 0},
        {"channels": False},
        {"order": 0},
        {"order": True},
        {"highpass_hz": 0.0},
        {"lowpass_hz": 4_000.0},
        {"highpass_hz": 1_000.0, "lowpass_hz": 500.0},
    ],
)
def test_band_limit_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    configuration: dict[str, object] = {"sample_rate": 8_000, "channels": 2}
    configuration.update(kwargs)
    with pytest.raises(ValueError):
        StreamingBandLimit(**configuration)


def test_band_limit_requires_scipy_when_filtering(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dsp, "scipy_signal", None)
    with pytest.raises(RuntimeError, match="SciPy"):
        StreamingBandLimit(8_000, 1, lowpass_hz=1_000.0)


@pytest.mark.parametrize(
    "arguments",
    [
        (0, 1_000.0, 1),
        (True, 1_000.0, 1),
        (8_000, 1_000.0, 0),
        (8_000, 1_000.0, False),
        (8_000, 0.0, 1),
        (8_000, float("nan"), 1),
        (8_000, "1000", 1),
        (8_000, 4_000.0, 1),
    ],
)
def test_high_pass_rejects_invalid_configuration(arguments: tuple[object, object, object]) -> None:
    with pytest.raises(ValueError):
        StreamingHighPass(*arguments)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"sample_rate": 0},
        {"sample_rate": True},
        {"order": float("nan")},
        {"order": 1.5},
        {"sections": 0},
        {"sections": False},
        {"low_hz": 0.0},
        {"low_hz": float("inf")},
        {"low_hz": "20"},
        {"high_hz": 0.0},
        {"high_hz": 4_000.0},
        {"high_hz": float("nan")},
        {"high_hz": "3000"},
    ],
)
def test_fractional_derivative_rejects_invalid_configuration(kwargs: dict[str, object]) -> None:
    configuration: dict[str, object] = {"sample_rate": 8_000}
    configuration.update(kwargs)
    with pytest.raises(ValueError):
        fractional_derivative_sos(**configuration)


def test_fractional_derivative_preserves_default_frequency_range() -> None:
    sos = fractional_derivative_sos(8_000)
    assert sos is not None
    assert np.isfinite(sos).all()


def test_band_limit_is_an_exact_bypass_without_cutoffs() -> None:
    samples = np.arange(12, dtype=np.float32).reshape(6, 2)
    filter_ = StreamingBandLimit(8_000, 2)
    output = filter_.process(samples)
    np.testing.assert_array_equal(output, samples.astype(np.float64))


def test_band_limit_matches_whole_block_and_chunked_stereo_processing() -> None:
    rng = np.random.default_rng(42)
    samples = rng.normal(size=(12_345, 2))
    whole = StreamingBandLimit(8_000, 2, highpass_hz=250.0, lowpass_hz=1_800.0, order=4)
    chunked = StreamingBandLimit(8_000, 2, highpass_hz=250.0, lowpass_hz=1_800.0, order=4)

    expected = whole.process(samples)
    actual = np.vstack((chunked.process(samples[:137]), chunked.process(samples[137:4_321]), chunked.process(samples[4_321:])))
    np.testing.assert_allclose(actual, expected, rtol=0, atol=1e-12)
    assert np.isfinite(expected).all()
    # SOS coefficients have no public accessor; output finiteness is checked above.
    assert all(np.isfinite(section.sos).all() for section in whole._filters)


@pytest.mark.parametrize("cutoff", ["highpass_hz", "lowpass_hz"])
def test_band_limit_attenuates_tones_outside_configured_band(cutoff: str) -> None:
    sample_rate = 8_000
    frames = 32_000
    time = np.arange(frames) / sample_rate
    in_band = np.sin(2 * np.pi * 700.0 * time)
    outside = np.sin(2 * np.pi * (50.0 if cutoff == "highpass_hz" else 2_500.0) * time)
    configuration = {cutoff: 300.0 if cutoff == "highpass_hz" else 1_200.0}
    retained_filter = StreamingBandLimit(sample_rate, 1, **configuration)
    attenuated_filter = StreamingBandLimit(sample_rate, 1, **configuration)

    in_band_rms = np.sqrt(np.mean(retained_filter.process(in_band[:, None])[-16_000:, 0] ** 2))
    outside_rms = np.sqrt(np.mean(attenuated_filter.process(outside[:, None])[-16_000:, 0] ** 2))
    input_rms = np.sqrt(np.mean(in_band[-16_000:] ** 2))
    assert 0.5 < in_band_rms / input_rms < 1.5
    assert outside_rms < in_band_rms * 0.35


def test_band_limit_rejects_wrong_input_shape_and_handles_empty_blocks() -> None:
    filter_ = StreamingBandLimit(8_000, 2, lowpass_hz=1_000.0)
    with pytest.raises(ValueError):
        filter_.process(np.zeros(10))
    with pytest.raises(ValueError):
        filter_.process(np.zeros((10, 1)))
    empty = filter_.process(np.empty((0, 2)))
    assert empty.shape == (0, 2)
