"""Streaming colored-noise audio and black-video generation."""

from .config import AudioConfig, Duration
from .mixer import NoiseMixer, normalize_ratios, parse_mix, power_to_amplitude_gains

__all__ = [
    "AudioConfig",
    "Duration",
    "NoiseMixer",
    "normalize_ratios",
    "parse_mix",
    "power_to_amplitude_gains",
]

__version__ = "0.1.0"
