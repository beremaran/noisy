from __future__ import annotations

import math

import pytest

from noisy.mixer import MixError, normalize_ratios, parse_mix, power_to_amplitude_gains


def test_parse_mix_normalizes_power_ratios() -> None:
    assert parse_mix("brown=70,pink=30") == pytest.approx({"brown": 0.7, "pink": 0.3})
    assert parse_mix("brown=7,pink=3") == pytest.approx({"brown": 0.7, "pink": 0.3})
    assert parse_mix("brown=0.7,pink=0.3") == pytest.approx({"brown": 0.7, "pink": 0.3})


def test_aliases_duplicates_and_zero_sources() -> None:
    assert normalize_ratios({"brown": 1, "red": 2, "pink": 0}) == pytest.approx(
        {"brown": 1.0}
    )


def test_power_ratios_use_square_root_amplitude_gains() -> None:
    gains = power_to_amplitude_gains({"brown": 70, "pink": 30})
    assert gains["brown"] == pytest.approx(math.sqrt(0.7))
    assert gains["pink"] == pytest.approx(math.sqrt(0.3))
    assert sum(gain * gain for gain in gains.values()) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "brown",
        "brown=",
        "=70",
        "brown=not-a-number",
        "unknown=1",
        "brown=-1",
        "brown=0,pink=0",
        "brown=nan",
        "brown=inf",
        "brown=1,,pink=2",
    ],
)
def test_invalid_mix_specs_are_actionable(spec: str) -> None:
    with pytest.raises(MixError):
        parse_mix(spec)
