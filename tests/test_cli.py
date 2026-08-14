from __future__ import annotations

import pytest

from noisy import cli
from noisy.cli import _resolve_output_cutoffs, build_parser
from noisy.pipeline import GenerationStats


def test_cli_output_cutoff_defaults() -> None:
    args = build_parser().parse_args(["--mix", "brown=100"])
    assert _resolve_output_cutoffs(args.sample_rate, args.highpass_hz, args.lowpass_hz) == (20.0, 17_000.0)


def test_cli_output_cutoff_defaults_follow_sample_rate() -> None:
    args = build_parser().parse_args(["--mix", "brown=100", "--sample-rate", "16000"])
    assert _resolve_output_cutoffs(args.sample_rate, args.highpass_hz, args.lowpass_hz) == (20.0, 7_200.0)


def test_cli_zero_disables_each_output_cutoff() -> None:
    args = build_parser().parse_args(
        ["--mix", "brown=100", "--highpass-hz", "0", "--lowpass-hz", "0"]
    )
    assert _resolve_output_cutoffs(args.sample_rate, args.highpass_hz, args.lowpass_hz) == (None, None)


@pytest.mark.parametrize(
    ("cutoff_args", "expected"),
    [([], (20.0, 3_600.0)), (["--highpass-hz", "0"], (None, 3_600.0))],
)
def test_cli_run_propagates_resolved_output_cutoffs(
    monkeypatch, tmp_path, cutoff_args: list[str], expected: tuple[float | None, float | None]
) -> None:
    captured = {}

    def fake_generate_audio(**kwargs):
        captured.update(kwargs)
        duration = kwargs["duration"]
        return GenerationStats(duration.frames, duration.seconds, kwargs["output_path"])

    monkeypatch.setattr(cli, "generate_audio", fake_generate_audio)
    output = tmp_path / "output.flac"
    args = build_parser().parse_args(
        ["--mix", "white=100", "--seconds", "0.01", "--sample-rate", "8_000", *cutoff_args,
         "--audio-output", str(output)]
    )

    assert cli._run(args) == 0
    config = captured["config"]
    assert (config.output_highpass_hz, config.output_lowpass_hz) == expected
