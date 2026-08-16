# noisy

`noisy` generates long-duration colored-noise audio in bounded memory and can
optionally mux it into a video whose every frame is pure black. It advances
stateful DSP generators one chunk at a time and writes each chunk immediately
with `soundfile`, so hours-long jobs do not require a full-duration waveform in
memory.

[![CI](https://github.com/beremaran/noisy/actions/workflows/ci.yml/badge.svg)](https://github.com/beremaran/noisy/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) [![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

For the complete command reference, filter behavior, mixing model, validation
rules, and long-master workflow, see the [user guide](docs/USER-GUIDE.md).

## Install

Install the project and its test dependencies with uv:

```bash
uv sync --extra test
```

Run the installed command with:

```bash
uv run noisy --help
```

To run it directly from this checkout without installing it into the project
environment, use uvx:

```bash
uvx --from . noisy --help
```

FFmpeg is required only for `--video-output`. It must be installed separately
and available as `ffmpeg` on `PATH`. On macOS, Homebrew provides it with
`brew install ffmpeg`; on Debian/Ubuntu, use the distribution package manager.

## Examples

Audio only, with a reproducible ten-hour mono FLAC master:

```bash
uv run noisy \
  --mix pink=70,brown=30 \
  --hours 10 \
  --sample-rate 48000 \
  --channels 1 \
  --target-rms-db -18 \
  --seed 1234 \
  --audio-output pink_brown.flac
```

Audio plus a compatibility-oriented MP4 copy:

```bash
uv run noisy \
  --mix brown=60,pink=30,white=10 \
  --hours 10 \
  --sample-rate 48000 \
  --channels 1 \
  --target-rms-db -18 \
  --audio-output mixed_noise.flac \
  --video-output mixed_noise_black.mp4
```

An MKV output uses H.264 video and FLAC audio by default, preserving a
lossless audio track:

```bash
uv run noisy \
  --mix brown=100 \
  --hours 1 \
  --audio-output brown.flac \
  --video-output brown_black.mkv
```

Use `--fps 30` for a conventional frame rate, or leave the default at 1 fps.
Because the picture is mathematically identical black on every frame, 1 fps is
enough for a visually identical result and keeps the video encode lightweight.
The resolution defaults to `1920x1080` and can be changed with, for example,
`--resolution 1280x720`.

## Published videos

These black-screen noise videos were generated with `scripts/generate-youtube.sh`
and are published on YouTube.

| Video | Duration | Watch |
| --- | --- | --- |
| Warm Pink Noise for Deep Relaxation & Sleep \| 10 Hours, Black Screen | 10 hours | [Watch on YouTube](https://youtu.be/fcc6-h9XU1w) |
| Pink and Brown Noise for Sleep & Sound Masking \| 10 Hours, Black Screen | 10 hours | [Watch on YouTube](https://youtu.be/ShaSYd97S10) |
| Pink Noise for Sleep, Relaxation & Focus \| 10 Hours, Black Screen | 10 hours | [Watch on YouTube](https://youtu.be/YQCeorh4tow) |
| Soft White Noise for Sleep & Sound Masking \| 10 Hours, Black Screen | 10 hours | [Watch on YouTube](https://youtu.be/DWwA5f1Iup8) |
| Deep Brown Noise for Sleep, Focus & Relaxation \| 10 Hours, Black Screen | 10 hours | [Watch on YouTube](https://youtu.be/L5zlCrAuRM0) |

See [the upload guide](docs/youtube-upload-guide.md) for titles, descriptions,
and tags; [the master guide](docs/youtube-master-guide.md) for generation and
pre-upload QA; and `scripts/generate-youtube.sh` to regenerate them.

## Noise colors

The color describes how power is distributed over frequency, not a timbral
label for a particular instrument:

| Color | Approximate power spectrum | Intuition |
| --- | --- | --- |
| White | constant | equal power per Hz |
| Pink | 1/f | about -3 dB per octave |
| Brown / red | 1/f² | about -6 dB per octave |
| Blue | f | about +3 dB per octave |
| Violet | f² | about +6 dB per octave |

White noise uses NumPy's modern `default_rng`. Pink noise uses a stateful
parallel IIR pinking filter. Brown noise uses a bounded (leaky) integrator
followed by a configurable 12 Hz default high-pass to control random-walk
drift, DC, and subsonic energy. Blue noise uses a stable finite-order
fractional differentiator when SciPy is available, and violet noise uses a
stateful first difference. Every generator retains its filter state between
blocks, including separate state and random streams for left and right stereo
channels.

## Mix semantics

Mix syntax is a comma-separated list of non-negative weights:

```text
brown=70,pink=30
brown=7,pink=3
brown=0.7,pink=0.3
```

These forms are equivalent. The weights are normalized internally and treated
as approximate power contributions. Each source is first scaled to a common
unit-RMS reference, then receives an amplitude gain of:

```text
gain = sqrt(normalized_power_ratio)
```

Thus `brown=70,pink=30` uses approximately 70% brown power and 30% pink power
before the final master stage. Directly using `0.7 * brown + 0.3 * pink`
would instead specify amplitude weights and would not have those power
semantics.

The final mix receives one fixed master gain derived from `--target-rms-db`.
A smooth ceiling limiter protects against random peaks without recalculating a
new gain for every block. The default target is -18 dBFS RMS and the default
ceiling is 0.98 linear (-0.18 dBFS).

## Output bandwidth

The CLI applies a streaming output high-pass and low-pass after mixing and
before the fixed master gain and peak protection. At 48 kHz, omitted cutoffs
default to 20 Hz and 17 kHz. The low-pass default is reduced automatically to
45% of the selected sample rate when that is lower than 17 kHz. Pass an
explicit `0` to either CLI option to disable that side of the output filter:

```bash
uv run noisy --mix pink=100 --hours 1 \
  --highpass-hz 0 --lowpass-hz 12000 --audio-output pink-wide.flac
```

This CLI behavior is intentionally different from direct library use:
`AudioConfig.output_highpass_hz` and `AudioConfig.output_lowpass_hz` default to
`None`, and `None` disables that filter. Library cutoff values must be positive,
below Nyquist, and high-pass must be lower than low-pass when both are set.
The separate brown-noise drift-control filter also defaults to
`--brown-highpass-hz 12` and must be strictly below Nyquist; very low sample
rates may require overriding that option.

## Streaming and file formats

The default block is five seconds and can be changed with `--chunk-seconds`.
Only the current block, generator state, and writer buffers are resident in
memory; there is no full-duration waveform and no full-file FFT. A ten-hour
48 kHz recording therefore scales with the chunk size rather than with ten
hours of samples.

The preferred high-quality output is mono or stereo 48 kHz, 24-bit PCM in
FLAC. Noise compresses poorly, so FLAC may still be large, but it avoids the
classic RIFF WAV 4 GiB limit. Normal `.wav` output is PCM-24 and is rejected
before generation if its estimated size would exceed that limit; this tool does
not silently create an oversized broken RIFF file or claim RF64 support.

MP4 video uses H.264 (`libx264`), `yuv420p`, and AAC at 256 kbps by default for
broad container/player compatibility. MKV uses FLAC audio by default for a lossless
audio/video master. The `--audio-codec` option can override the video-track
codec, but the requested codec must be supported by both FFmpeg and the
selected container.

If only `--video-output` is supplied, the audio master is temporary and is
removed after muxing. Add `--keep-audio` to retain it; when no explicit
`--audio-output` is given, it is saved beside the video using the same stem and
a `.flac` suffix.

## Development and tests

Install the test extra and run:

```bash
uv sync --extra test
uv run pytest
```

The tests cover parsing and power-gain semantics, deterministic seeded output,
chunk continuity, duration and finite-sample guarantees, spectral slope
sanity checks, WAV size protection, and FFmpeg command construction.

Implementation details for contributors live in the Python module docstrings;
the [user guide](docs/USER-GUIDE.md) is the supported operational reference.

## License

MIT; see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).
