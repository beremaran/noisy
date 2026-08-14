import numpy as np
import pytest
import soundfile as sf

from noisy.config import AudioConfig, calculate_duration
from noisy.pipeline import generate_audio


def _generate(
    tmp_path,
    *,
    chunk_seconds: float,
    channels: int = 1,
    seconds: float = 2.0,
    calibration_seconds: float = 0.5,
    highpass_hz: float | None = None,
    lowpass_hz: float | None = 500.0,
):
    config = AudioConfig(
        sample_rate=8_000,
        channels=channels,
        chunk_seconds=chunk_seconds,
        calibration_seconds=calibration_seconds,
        output_highpass_hz=highpass_hz,
        output_lowpass_hz=lowpass_hz,
    )
    duration = calculate_duration(seconds=seconds, sample_rate=config.sample_rate)
    path = tmp_path / f"output-{chunk_seconds}.flac"
    stats = generate_audio(
        power_ratios={"white": 1.0},
        duration=duration,
        config=config,
        output_path=str(path),
        seed=1234,
        quiet=True,
    )
    samples, sample_rate = sf.read(path, always_2d=True)
    return config, stats, samples, sample_rate


def test_output_filter_is_applied_and_target_rms_is_stable(tmp_path) -> None:
    filtered_config, stats, filtered, sample_rate = _generate(
        tmp_path, chunk_seconds=0.17
    )
    _, _, unfiltered, _ = _generate(tmp_path, chunk_seconds=0.17, lowpass_hz=None)

    assert not np.array_equal(filtered, unfiltered)
    assert stats.frames == 16_000
    assert sample_rate == filtered_config.sample_rate
    assert np.isfinite(filtered).all()
    assert np.sqrt(np.mean(np.square(filtered))) == pytest.approx(
        filtered_config.target_rms_linear, abs=0.02
    )
    assert np.max(np.abs(filtered)) <= filtered_config.peak_ceiling + 1e-6


def test_filtered_target_rms_stays_stable_after_short_calibration(tmp_path) -> None:
    config, _, samples, _ = _generate(
        tmp_path,
        chunk_seconds=0.17,
        seconds=2.0,
        calibration_seconds=0.01,
        lowpass_hz=40.0,
    )

    assert np.sqrt(np.mean(np.square(samples))) == pytest.approx(
        config.target_rms_linear, abs=0.03
    )


def test_filtered_output_is_chunk_size_deterministic(tmp_path) -> None:
    _, first_stats, first, first_rate = _generate(tmp_path, chunk_seconds=0.11)
    _, second_stats, second, second_rate = _generate(tmp_path, chunk_seconds=0.37)

    assert first_stats.frames == second_stats.frames == 16_000
    assert first_rate == second_rate == 8_000
    np.testing.assert_array_equal(first, second)


def test_stereo_filtered_output_preserves_requested_frames_and_ceiling(tmp_path) -> None:
    config, stats, samples, sample_rate = _generate(
        tmp_path,
        chunk_seconds=0.13,
        channels=2,
        seconds=0.37,
    )

    assert stats.frames == calculate_duration(seconds=0.37, sample_rate=8_000).frames
    assert samples.shape == (stats.frames, 2)
    assert sample_rate == config.sample_rate
    assert np.isfinite(samples).all()
    assert np.max(np.abs(samples)) <= config.peak_ceiling + 1e-6


@pytest.mark.parametrize(
    ("highpass_hz", "lowpass_hz"),
    [(100.0, None), (None, 3_000.0)],
)
def test_filtered_output_supports_one_sided_filters(
    tmp_path, highpass_hz: float | None, lowpass_hz: float | None
) -> None:
    _, stats, samples, sample_rate = _generate(
        tmp_path,
        chunk_seconds=0.17,
        seconds=0.2,
        highpass_hz=highpass_hz,
        lowpass_hz=lowpass_hz,
    )

    assert stats.frames == 1_600
    assert samples.shape == (1_600, 1)
    assert sample_rate == 8_000
    assert np.isfinite(samples).all()


def test_filtered_output_can_be_shorter_than_calibration_block(tmp_path) -> None:
    _, stats, samples, _ = _generate(
        tmp_path,
        chunk_seconds=0.17,
        seconds=0.1,
        calibration_seconds=1.0,
    )

    assert stats.frames == 800
    assert samples.shape == (800, 1)
    assert np.isfinite(samples).all()
