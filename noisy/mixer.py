"""Mix parsing and power-aware source mixing."""

from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np

from .generators import NoiseGenerator, canonical_color, make_generator


class MixError(ValueError):
    """Raised for malformed or unusable mix specifications."""


def normalize_ratios(ratios: Mapping[str, float]) -> dict[str, float]:
    """Validate and normalize color ratios into power fractions.

    Duplicate canonical names are combined, so ``brown=1,red=2`` is accepted
    and treated as three parts of brown noise.  Zero-valued sources are
    dropped, which avoids constructing an unnecessary generator.
    """

    if not isinstance(ratios, Mapping) or not ratios:
        raise MixError("mix must contain at least one noise color")

    combined: dict[str, float] = {}
    for raw_color, raw_ratio in ratios.items():
        if not isinstance(raw_color, str) or not raw_color.strip():
            raise MixError("noise color names must be non-empty strings")
        try:
            color = canonical_color(raw_color)
        except ValueError as exc:
            raise MixError(str(exc)) from exc

        if isinstance(raw_ratio, bool):
            raise MixError(f"ratio for {raw_color!r} must be numeric")
        try:
            ratio = float(raw_ratio)
        except (TypeError, ValueError) as exc:
            raise MixError(f"ratio for {raw_color!r} must be numeric") from exc
        if not math.isfinite(ratio):
            raise MixError(f"ratio for {raw_color!r} must be finite")
        if ratio < 0:
            raise MixError(f"ratio for {raw_color!r} must not be negative")
        combined[color] = combined.get(color, 0.0) + ratio

    total = sum(combined.values())
    if not math.isfinite(total) or total <= 0:
        raise MixError("mix ratios must have a total greater than zero")
    return {color: ratio / total for color, ratio in combined.items() if ratio > 0}


def parse_mix(specification: str) -> dict[str, float]:
    """Parse ``color=ratio,color=ratio`` and return normalized power ratios."""

    if not isinstance(specification, str) or not specification.strip():
        raise MixError("--mix must not be empty; use for example brown=70,pink=30")

    raw_ratios: dict[str, float] = {}
    parts = specification.split(",")
    for part in parts:
        item = part.strip()
        if not item:
            raise MixError("--mix contains an empty component")
        if item.count("=") != 1:
            raise MixError(
                f"invalid mix component {item!r}; expected color=ratio, for example pink=70"
            )
        raw_color, raw_ratio = (piece.strip() for piece in item.split("=", 1))
        if not raw_color:
            raise MixError(f"invalid mix component {item!r}; color name is missing")
        if not raw_ratio:
            raise MixError(f"invalid mix component {item!r}; ratio is missing")
        try:
            parsed_ratio = float(raw_ratio)
        except ValueError as exc:
            raise MixError(
                f"invalid ratio {raw_ratio!r} for {raw_color!r}; use a finite number"
            ) from exc
        raw_ratios[raw_color] = raw_ratios.get(raw_color, 0.0) + parsed_ratio

    return normalize_ratios(raw_ratios)


def power_to_amplitude_gains(power_ratios: Mapping[str, float]) -> dict[str, float]:
    """Convert requested power contributions into amplitude gains.

    Ratios are normalized before taking the square root, so both percentages
    and arbitrary positive weights are accepted.
    """

    normalized = normalize_ratios(power_ratios)
    return {color: math.sqrt(ratio) for color, ratio in normalized.items()}


def _spawn_source_seeds(seed: int | None, count: int) -> list[int | None]:
    if seed is None:
        return [None] * count
    if not isinstance(seed, (int, np.integer)) or isinstance(seed, bool):
        raise MixError("seed must be an integer")
    sequence = np.random.SeedSequence(int(seed))
    children = sequence.spawn(count)
    return [int(child.generate_state(1)[0]) for child in children]


class NoiseMixer:
    """Generate normalized sources and combine them using power semantics."""

    def __init__(
        self,
        power_ratios: Mapping[str, float],
        sample_rate: int,
        channels: int,
        *,
        seed: int | None = None,
        calibration_seconds: float = 4.0,
        brown_highpass_hz: float = 12.0,
    ) -> None:
        self.power_ratios = normalize_ratios(power_ratios)
        self.amplitude_gains = power_to_amplitude_gains(self.power_ratios)
        colors = list(self.power_ratios)
        source_seeds = _spawn_source_seeds(seed, len(colors))
        self.generators: dict[str, NoiseGenerator] = {
            color: make_generator(
                color,
                sample_rate,
                channels,
                seed=source_seed,
                calibration_seconds=calibration_seconds,
                brown_highpass_hz=brown_highpass_hz,
            )
            for color, source_seed in zip(colors, source_seeds)
        }
        self.sample_rate = sample_rate
        self.channels = channels

    @property
    def expected_rms(self) -> float:
        """Expected RMS of independent unit-RMS sources before mastering."""

        return math.sqrt(sum(self.power_ratios.values()))

    def generate(self, frames: int) -> np.ndarray:
        """Generate one mixed block and advance every source by that block."""

        if not isinstance(frames, (int, np.integer)) or isinstance(frames, bool):
            raise MixError("frame count must be an integer")
        if frames < 0:
            raise MixError("frame count must not be negative")
        mixed = np.zeros((int(frames), self.channels), dtype=np.float64)
        for color, generator in self.generators.items():
            # Every source has a fixed unit-RMS reference.  The square-root
            # gain is the key distinction between power ratios and direct
            # amplitude weights.
            mixed += self.amplitude_gains[color] * generator.generate(int(frames))
        return mixed


__all__ = [
    "MixError",
    "NoiseMixer",
    "normalize_ratios",
    "parse_mix",
    "power_to_amplitude_gains",
]
