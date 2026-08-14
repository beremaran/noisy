from __future__ import annotations

import numpy as np
import pytest

soundfile = pytest.importorskip("soundfile")

from noisy.audio import (
    RIFF_FILE_SIZE_LIMIT,
    apply_master_gain,
    estimate_pcm24_bytes,
    validate_audio_output,
)
from noisy.config import AudioConfig, ConfigurationError, calculate_duration
from noisy.mixer import parse_mix
from noisy.pipeline import generate_audio


def test_duration_is_sample_accurate() -> None:
    duration = calculate_duration(hours=1, minutes=2, seconds=3.25, sample_rate=48_000)
    assert duration.frames == 48_000 * 3723 + 12_000
    assert duration.seconds == pytest.approx(3723.25)


def test_duration_rejects_zero() -> None:
    with pytest.raises(ConfigurationError):
        calculate_duration(sample_rate=48_000)


def test_wav_size_guard_rejects_classic_riff_overflow(tmp_path) -> None:
    frames = 1_728_000_000  # 10 hours at 48 kHz
    estimated = estimate_pcm24_bytes(frames, 1)
    assert estimated > RIFF_FILE_SIZE_LIMIT
    with pytest.raises(ValueError, match="RIFF WAV"):
        validate_audio_output(tmp_path / "ten-hours.wav", frames, 1)
    assert validate_audio_output(tmp_path / "ten-hours.flac", frames, 1) == "FLAC"


def test_master_gain_has_a_fixed_target_and_peak_ceiling() -> None:
    samples = np.array([[0.0], [1.0], [-4.0], [0.5]], dtype=np.float64)
    mastered = apply_master_gain(
        samples,
        target_rms_linear=10 ** (-18 / 20),
        expected_mix_rms=1.0,
        peak_ceiling=0.98,
    )
    assert mastered.dtype == np.float32
    assert np.isfinite(mastered).all()
    assert np.max(np.abs(mastered)) <= 0.98


def test_supported_audio_defaults() -> None:
    config = AudioConfig()
    assert config.sample_rate == 48_000
    assert config.channels == 1
    assert config.chunk_frames == 240_000
    assert config.output_highpass_hz is None
    assert config.output_lowpass_hz is None


def test_legacy_positional_calibration_seconds_position_is_preserved() -> None:
    config = AudioConfig(48_000, 1, 5.0, -18.0, 0.98, 12.0, 2.5)
    assert config.calibration_seconds == 2.5
    assert config.output_highpass_hz is None
    assert config.output_lowpass_hz is None


@pytest.mark.parametrize(
    "field",
    [
        "chunk_seconds",
        "target_rms_db",
        "peak_ceiling",
        "brown_highpass_hz",
        "calibration_seconds",
        "output_highpass_hz",
        "output_lowpass_hz",
    ],
)
def test_audio_config_rejects_bool_numeric_values(field: str) -> None:
    with pytest.raises(ConfigurationError):
        AudioConfig(**{field: True})


@pytest.mark.parametrize("field", ["chunk_seconds", "brown_highpass_hz", "output_lowpass_hz"])
def test_audio_config_rejects_non_numeric_values(field: str) -> None:
    with pytest.raises(ConfigurationError):
        AudioConfig(**{field: "not-a-number"})


def test_audio_config_rejects_bool_channels() -> None:
    with pytest.raises(ConfigurationError):
        AudioConfig(channels=True)


@pytest.mark.parametrize("field", ["output_highpass_hz", "output_lowpass_hz"])
@pytest.mark.parametrize("value", [True, "20", float("nan"), float("inf"), 0.0, -1.0, 24_000.0])
def test_output_cutoffs_reject_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError):
        AudioConfig(**{field: value})


def test_output_cutoffs_require_highpass_below_lowpass() -> None:
    with pytest.raises(ConfigurationError, match="below the output low-pass"):
        AudioConfig(output_highpass_hz=1_000.0, output_lowpass_hz=1_000.0)


def test_output_cutoffs_use_selected_sample_rate_nyquist() -> None:
    config = AudioConfig(sample_rate=8_000, output_highpass_hz=20.0, output_lowpass_hz=3_000.0)
    assert config.output_lowpass_hz == 3_000.0
    with pytest.raises(ConfigurationError):
        AudioConfig(sample_rate=8_000, output_lowpass_hz=4_000.0)


@pytest.mark.parametrize("field", ["hours", "minutes", "seconds"])
@pytest.mark.parametrize("value", [True, "1"])
def test_duration_rejects_bool_and_non_numeric_values(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError):
        calculate_duration(**{field: value, "seconds": 1.0} if field != "seconds" else {field: value})


def test_streaming_writer_preserves_requested_duration_and_ceiling(tmp_path) -> None:
    output = tmp_path / "short.flac"
    sample_rate = 8_000
    duration = calculate_duration(seconds=0.75, sample_rate=sample_rate)
    stats = generate_audio(
        power_ratios=parse_mix("brown=60,pink=30,white=10"),
        duration=duration,
        config=AudioConfig(
            sample_rate=sample_rate,
            channels=1,
            chunk_seconds=0.11,
            target_rms_db=-6.0,
        ),
        output_path=str(output),
        seed=42,
        quiet=True,
    )
    info = soundfile.info(output)
    samples, rate = soundfile.read(output, always_2d=True)
    assert stats.frames == duration.frames == info.frames == samples.shape[0]
    assert rate == sample_rate
    assert info.channels == 1
    assert np.isfinite(samples).all()
    assert np.max(np.abs(samples)) <= 0.98 + 1e-6
