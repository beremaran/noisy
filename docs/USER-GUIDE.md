# noisy User Guide

`noisy` creates mono or stereo colored-noise masters for audio-only delivery or
for muxing into a pure-black video. It is intended for long recordings such as
sleep, focus, masking, and ambient-noise masters. The tool does not download,
upload, or publish media.

## Install

The project requires Python 3.10 or newer. From a checkout, install the normal
dependencies with `uv`:

```bash
uv sync
```

Install the test extra when developing or validating a change:

```bash
uv sync --extra test
```

Check the installed command:

```bash
uv run noisy --help
uv run noisy --version
```

FFmpeg is required only when `--video-output` is requested. It must be named
`ffmpeg` on `PATH`:

```bash
# macOS
brew install ffmpeg

# Debian/Ubuntu
sudo apt install ffmpeg
```

Without installing the checkout into the project environment, `uvx --from .`
can run the command directly:

```bash
uvx --from . noisy --help
```

## Quick Start

Generate a ten-hour, mono, 48 kHz FLAC master with a reproducible brown/pink
mix:

```bash
uv run noisy \
  --mix brown=70,pink=30 \
  --hours 10 \
  --sample-rate 48000 \
  --channels 1 \
  --target-rms-db -18 \
  --seed 1234 \
  --audio-output pink-brown-10h.flac
```

Generate an audio master and an MP4 containing the same audio with black video:

```bash
uv run noisy \
  --mix brown=60,pink=30,white=10 \
  --hours 10 \
  --audio-output mixed.flac \
  --video-output mixed-black.mp4
```

For a lossless audio track in a Matroska container, use MKV. The default MKV
audio codec is FLAC; the default MP4 audio codec is AAC at 256 kbps:

```bash
uv run noisy \
  --mix brown=100 \
  --hours 1 \
  --audio-output brown.flac \
  --video-output brown-black.mkv
```

An output path is required. Use `--force` to overwrite existing output files.

## Processing Model

### Streaming and memory

The generator produces one block at a time. For each block, it advances every
selected noise source, mixes the results, applies the optional output filter,
applies mastering, and writes the block to the audio file. Stateful IIR, FIR,
SOS, differentiator, and brown-noise integrator histories are retained across
blocks, including independent channel state for stereo.

Only the current block, persistent DSP state, calibration data, and writer
buffers are resident. There is no full-duration waveform, full-file FFT, or
per-block level normalization. Memory therefore follows `--chunk-seconds`,
not the requested hours. A larger chunk can improve throughput at the cost of
more working memory; a smaller chunk reduces the block footprint.

The default chunk is five seconds. Chunk boundaries do not reset filters or
random streams and do not pump the level. With the same seed and all other
settings held constant, the generated sample stream is deterministic even if
chunk duration changes.

### Filter order

The final output bandwidth filter is deliberately after source mixing:

1. Generate each colored source and normalize it to its fixed unit-RMS
   reference.
2. Combine sources using power-ratio amplitude gains.
3. Apply the optional output high-pass and low-pass cascade to the complete mix.
4. Estimate a settled RMS during discarded filter calibration and compute one
   fixed master gain for the stream.
5. Apply the fixed gain and gentle peak protection, then write the block.

Filtering after mixing gives the final file one shared bandwidth. It also lets
the master gain target the level of the filtered signal rather than the
unfiltered source estimate. The gain is not recomputed at chunk boundaries.
The filter is causal and stateful; its calibration pre-roll is discarded so
startup transients are not emitted.

This output filter is separate from brown noise's internal drift-control
high-pass. Brown always uses a positive `--brown-highpass-hz` corner, 12 Hz by
default, to control random-walk drift, DC, and subsonic energy. The output
high-pass is a final mix-wide tone/bandwidth choice and defaults to 20 Hz in the
CLI.

## Output Bandwidth

The CLI resolves omitted output cutoffs as follows:

| CLI option | Omitted at 48 kHz | Resolution rule | Explicit `0` |
| --- | ---: | --- | --- |
| `--highpass-hz` | 20 Hz | `min(20, 0.1 * sample_rate)` | Disable output high-pass |
| `--lowpass-hz` | 17,000 Hz | `min(17000, 0.45 * sample_rate)` | Disable output low-pass |

At 48 kHz, the effective default band is 20 Hz to 17 kHz. At 16 kHz, for
example, the effective low-pass default is 7,200 Hz, not 17 kHz. This fallback
keeps the default below Nyquist for lower sample rates.
The separate brown-noise drift-control high-pass is also subject to Nyquist:
its default `--brown-highpass-hz 12` must be strictly below Nyquist, so a very
low sample rate may require overriding that option to a lower positive value.

Use explicit values when producing a controlled master:

```bash
# Band-limit a bright mix to a gentler 20 Hz to 10 kHz band.
uv run noisy --mix white=100 --hours 1 \
  --highpass-hz 20 --lowpass-hz 10000 --audio-output white-10k.flac

# Disable both CLI output cutoffs. Brown's internal 12 Hz drift control remains.
uv run noisy --mix brown=100 --hours 1 \
  --highpass-hz 0 --lowpass-hz 0 --audio-output brown-unbandlimited.flac
```

`0` has special meaning only in the CLI. Direct library construction uses
`AudioConfig` fields whose defaults are both `None`; `None` disables the
corresponding output filter. A direct library value of `0` is invalid, not a
disable request. Direct values must be finite, strictly greater than zero,
strictly below Nyquist, and, when both are present, high-pass must be below
low-pass. The CLI also rejects invalid nonzero values through this same config
validation.

### Perceived harshness

The color controls spectral power, while the output low-pass controls how much
of the upper spectrum reaches the listener. Lowering the low-pass generally
removes more hiss-like brightness and edge; raising it preserves more air and
detail but can make bright colors tiring. Raising the high-pass removes rumble
and very low-frequency weight, but can make brown-heavy mixes feel thinner.
These are starting points, not presets or guarantees for every playback system:

| Source or mix | Gentle starting point | Why start here |
| --- | --- | --- |
| White | `--highpass-hz 20 --lowpass-hz 12000` | White has equal power per Hz, so the upper band can dominate perceived hiss. |
| Pink | `--highpass-hz 20 --lowpass-hz 16000` | Pink is less bright than white and often tolerates a wider band. |
| Brown | `--highpass-hz 20 --lowpass-hz 10000` | Brown is low-weighted; the final high-pass controls rumble without changing its internal drift guard. |
| Blue | `--highpass-hz 20 --lowpass-hz 9000` | Blue emphasizes higher frequencies and benefits from a conservative top end. |
| Violet | `--highpass-hz 20 --lowpass-hz 7000` | Violet is the brightest listed color; start narrow and widen only if needed. |

For a mixed bed, begin with the brightest component at a modest power ratio,
then adjust the low-pass by ear. For example:

```bash
uv run noisy --mix brown=60,pink=30,white=10 --hours 8 \
  --highpass-hz 20 --lowpass-hz 12000 \
  --target-rms-db -18 --seed 42 --audio-output bed.flac
```

## Noise Colors and Mixes

Supported colors are `white`, `pink`, `brown`, `blue`, and `violet`.
`red` is accepted as an alias for `brown`.

| Color | Approximate power spectrum | Practical description |
| --- | --- | --- |
| White | Constant | Equal power per Hz; commonly perceived as bright. |
| Pink | `1/f` | About -3 dB per octave; balanced toward lower frequencies. |
| Brown / red | `1/f^2` | About -6 dB per octave; deep and low-weighted. |
| Blue | `f` | About +3 dB per octave; upper-frequency weighted. |
| Violet | `f^2` | About +6 dB per octave; strongly upper-frequency weighted. |

`--mix` is a comma-separated list of non-negative weights. The values are
normalized, so these requests are equivalent:

```text
brown=70,pink=30
brown=7,pink=3
brown=0.7,pink=0.3
```

The values express approximate power contributions, not direct amplitude
weights. Each source is first put on a unit-RMS reference, then receives:

```text
amplitude gain = sqrt(normalized power ratio)
```

Thus `brown=70,pink=30` means approximately 70% brown power and 30% pink
power before final filtering and mastering. Zero-valued sources are ignored;
the total must be greater than zero. Duplicate canonical names are combined,
so `brown=1,red=2` is accepted as three parts brown.

## Level, Reproducibility, and Duration

### RMS and peak ceiling

`--target-rms-db` sets the fixed master RMS target in dBFS. It defaults to
`-18`, which corresponds to a linear target of approximately `0.126`. The
target must be at or below 0 dBFS. It is a reference for the stream, not a
promise that every short block has exactly that RMS.

`--peak-ceiling` is a linear full-scale ceiling from greater than 0 through 1;
the default is `0.98` (about -0.18 dBFS). A smooth saturating soft-knee curve
starts at 90% of the ceiling and keeps random peaks at or below the configured
ceiling. This is peak protection, not block-by-block normalization.

### Seed

`--seed` accepts an integer. It seeds independent source and channel random
streams, making a run reproducible with the same mix, sample rate, channel
count, filter settings, duration, and package implementation. Omitting it uses
fresh random generators. Reusing a seed does not make different mixes or
settings identical.

### Duration and sample accuracy

Use any combination of `--hours`, `--minutes`, and `--seconds`; each is
non-negative and may be fractional. The combined duration must be greater than
zero. The total is converted to a single rounded frame count at the selected
sample rate, so the file duration is sample-accurate and does not accumulate
floating-point error across chunks.

Defaults are 48,000 Hz and mono. `--channels` accepts only `1` or `2`.

## CLI Reference

| Option | Default | Meaning and constraints |
| --- | --- | --- |
| `--mix` | Required | Comma-separated non-negative power ratios. At least one positive total is required. |
| `--hours` | `0` | Non-negative duration component. |
| `--minutes` | `0` | Non-negative duration component. |
| `--seconds` | `0` | Non-negative duration component. Combined duration must be positive. |
| `--sample-rate` | `48000` | Positive integer sample rate. Filter cutoffs must fit below its Nyquist frequency. |
| `--channels` | `1` | `1` for mono or `2` for stereo. |
| `--chunk-seconds` | `5.0` | Positive streaming block duration. |
| `--target-rms-db` | `-18.0` | Fixed master RMS target; must be at or below 0 dBFS. |
| `--peak-ceiling` | `0.98` | Positive linear ceiling no greater than 1. |
| `--brown-highpass-hz` | `12.0` | Positive brown drift-control corner below Nyquist; not the final output high-pass. |
| `--highpass-hz` | CLI-resolved 20 Hz at 48 kHz | Final mix high-pass; omitted value is sample-rate-safe, `0` disables. |
| `--lowpass-hz` | CLI-resolved 17 kHz at 48 kHz | Final mix low-pass; omitted value is sample-rate-safe, `0` disables. |
| `--seed` | Unset | Integer seed for reproducible random streams. |
| `--audio-output` | Unset | `.flac` or `.wav`; required unless video output is supplied. |
| `--video-output` | Unset | `.mp4` or `.mkv`; requires FFmpeg. |
| `--resolution` | `1920x1080` | Positive `WIDTHxHEIGHT` for black video. |
| `--fps` | `1.0` | Positive video frame rate. |
| `--video-codec` | `libx264` | FFmpeg video codec name. |
| `--audio-codec` | AAC for MP4, FLAC for MKV | Override the video-track audio codec. The requested codec must be supported by both FFmpeg and the selected container; AAC is written at 256 kbps. |
| `--keep-audio` | Off | Keep the generated audio when only video output is requested; saves it beside the video as `.flac`. |
| `--force` | Off | Overwrite existing output files. |
| `--quiet` | Off | Suppress generation progress. |
| `--version` | N/A | Print the package version. |

## Files, Containers, and Video

Audio output supports FLAC and WAV and is written as 24-bit PCM. FLAC is the
recommended format for long masters because it avoids the classic RIFF WAV 4
GiB limit. Noise compresses poorly, so a FLAC file may still be large.

Before generation, the CLI estimates PCM-24 WAV size and rejects a request that
would exceed the 4 GiB RIFF limit. RF64 is not enabled. Choose `.flac` for a
large or multi-hour WAV-equivalent master.

Video is synthesized by FFmpeg as pure black at the requested resolution and
frame rate, then muxed with the generated audio. MP4 uses H.264, `yuv420p`, and
AAC at 256 kbps by default, with `+faststart`. MKV uses H.264 and FLAC audio by
default. Any override supplied through `--audio-codec` must be supported by
both FFmpeg and the selected container. The requested finite audio duration is
authoritative; video is not shortened merely because the frame rate is low.

When only `--video-output` is supplied, the audio file is temporary and removed
after muxing. Add `--keep-audio` to retain it; in that case, the CLI chooses a
`.flac` path beside the video. Supplying `--audio-output` always retains that
explicit audio file.

## Troubleshooting and Validation

| Symptom | Likely cause and remedy |
| --- | --- |
| `provide --audio-output, --video-output, or both` | Add at least one output path. |
| `output already exists` | Choose a new path or add `--force`. |
| Unknown color or invalid mix | Use supported names and `color=ratio` components; ratios cannot be negative and their total must be positive. |
| Cutoff must be below Nyquist | Lower the cutoff for the selected sample rate. At 16 kHz, the omitted CLI low-pass becomes 7.2 kHz. |
| High-pass must be below low-pass | Increase the low-pass or lower the high-pass. Remember that `0` disables only the CLI output side. |
| WAV RIFF limit error | Use a `.flac` output instead; RF64 is not enabled. |
| FFmpeg not found | Install FFmpeg and verify `ffmpeg` is on `PATH`; FFmpeg is needed only for video. |
| Video extension error | Use `.mp4` or `.mkv`. |
| Duration error | The combined hours, minutes, and seconds must be positive and must round to at least one frame. |
| Unexpected brightness or rumble | Adjust final `--lowpass-hz` or `--highpass-hz`; adjust `--brown-highpass-hz` only for brown's internal drift control. |

Validation happens before the expensive generation work where practical:
output paths and overwrite policy are checked, duration and audio format are
validated, WAV size is estimated, and FFmpeg availability/video extension are
checked before generating hours of audio. During generation, blocks are also
checked for finite samples and the peak protector asserts its ceiling.

## Local YouTube Delivery Preparation

This checklist covers creating and checking local files before delivery. The
tool only generates and muxes files; it does not upload them, verify YouTube's
acceptance rules, or predict platform-specific upload behavior. YouTube
transcodes published media, so a lossless local MKV/FLAC track does not
guarantee a lossless track after publication. For platform-specific upload
guidance, see the separate [YouTube upload guide](../outputs/YOUTUBE-NOISE-UPLOAD-GUIDE.md).

1. Install dependencies with `uv sync` and verify `uv run noisy --help`.
2. Install FFmpeg and verify `ffmpeg -version` if producing video.
3. Prefer 48 kHz, mono for a simple ambience master or stereo when independent
   left/right streams are desired.
4. Choose the mix using power ratios, not assumed amplitude percentages.
5. Start at `--target-rms-db -18` and the default `--peak-ceiling 0.98` unless
   a documented delivery requirement says otherwise.
6. Set explicit output cutoffs after listening to a short test; use a lower
   low-pass for white, blue, or violet if the result is harsh.
7. Keep brown's `--brown-highpass-hz` separate from the final output
   high-pass; the former controls source drift.
8. Use `--seed` for a repeatable master and record the complete command line.
9. Run a short smoke test first, for example with `--seconds 10`, and inspect
   the file's sample rate, channel count, duration, RMS, and peak.
10. Use FLAC for the retained audio master, especially for multi-hour work;
    do not rely on classic WAV for files near or above 4 GiB.
11. For the local delivery file, use `.mp4` for broad compatibility or `.mkv`
    when retaining a lossless local FLAC audio track is important. Neither
    choice guarantees a lossless published YouTube track.
12. Use `--quiet` only after the command is known to work; otherwise retain
    progress output for ETA and completion evidence.
13. Confirm the final local file plays from beginning to end and that the black
    video duration and audio duration are complete before delivery.
14. Preserve the command, seed, package version, output metadata, and any
    listening notes alongside the delivery file for reproducibility.

## Validation Commands

Install test dependencies and run the repository test suite:

```bash
uv sync --extra test
uv run pytest
```

For a local short-run check, write a temporary FLAC and inspect it with the
installed Python environment or an audio tool. The application itself reports
the generated frame count and duration when it completes:

```bash
uv run noisy --mix pink=70,brown=30 --seconds 10 \
  --seed 1234 --audio-output smoke.flac
```

The command must report a positive frame count, and the output should have the
requested sample rate and channel count, finite samples, an RMS near the
configured target over a sufficiently long sample, and no peak above the
configured ceiling.
