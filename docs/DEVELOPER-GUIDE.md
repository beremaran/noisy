# Developer Guide

This document describes the current implementation of `noisy` as it exists in
the repository. It is intended for contributors changing the streaming audio
path, the public configuration, or the optional black-video muxing path.

## Scope And Design Goals

`noisy` generates long colored-noise recordings without materializing the
recording in memory. The audio path is stateful and causal: every generator
and every streaming DSP stage retains the state needed to make processing a
sequence of blocks equivalent to processing one contiguous block. The normal
runtime dependencies are NumPy, SciPy, and `soundfile`; FFmpeg is required only
when video output is requested.

The principal invariants are:

- A requested duration is converted once to an integer frame count.
- Public generator output is finite, two-dimensional, and `float32`.
- Noise sources are calibrated once and then use fixed gains. Blocks are never
  independently normalized.
- Mix values represent power fractions, so source amplitudes use square roots.
- The output filter, when enabled, is after mixing and before mastering.
- Mastering uses one fixed gain and a bounded soft-knee peak curve; it does not
  use a per-block peak or RMS measurement.
- The writer receives exactly the requested number of frames.

## Module Map

| Module | Responsibility | Important symbols |
| --- | --- | --- |
| `noisy/config.py` | Validated audio settings and sample-accurate duration | `AudioConfig`, `Duration`, `calculate_duration`, `ConfigurationError` |
| `noisy/generators.py` | Stateful white, pink, brown, blue, and violet sources | `NoiseGenerator`, `make_generator`, `canonical_color`, generator subclasses |
| `noisy/dsp.py` | Stateful FIR/IIR/SOS primitives and filter design | `StreamingFIR`, `StreamingHighPass`, `StreamingSOS`, `StreamingBandLimit`, `fractional_derivative_sos` |
| `noisy/mixer.py` | Mix syntax, canonicalization, power semantics, source construction | `parse_mix`, `normalize_ratios`, `power_to_amplitude_gains`, `NoiseMixer`, `MixError` |
| `noisy/audio.py` | Fixed-gain mastering and streaming FLAC/WAV output | `apply_master_gain`, `StreamingAudioWriter`, `validate_audio_output`, `AudioError` |
| `noisy/pipeline.py` | End-to-end audio orchestration and progress | `generate_audio`, `_calibrate_output_filter`, `GenerationStats` |
| `noisy/cli.py` | Argument parsing, CLI-only cutoff defaults, validation, and output selection | `build_parser`, `_resolve_output_cutoffs`, `_run`, `main` |
| `noisy/video.py` | Pure-black FFmpeg source and audio muxing | `require_ffmpeg`, `build_ffmpeg_command`, `mux_black_video`, `VideoError` |
| `noisy/__init__.py` | Small library export surface and package version | `AudioConfig`, `Duration`, `NoiseMixer`, mix helpers |

`build/`, `dist/`, `noisy.egg-info/`, cached bytecode, and generated media are
artifacts, not implementation sources.

## End-To-End Call Graph

The CLI path is:

```text
noisy (entry point)
  -> noisy.cli.main(argv)
     -> build_parser().parse_args()
     -> _run(args)
        -> parse_mix(args.mix)
        -> _resolve_output_cutoffs(...)
        -> AudioConfig(...)
        -> calculate_duration(...)
        -> validate_audio_output(...)              [audio output]
        -> require_ffmpeg() / parse_resolution()   [video output]
        -> generate_audio(...)
           -> NoiseMixer(...)
              -> normalize_ratios()
              -> power_to_amplitude_gains()
              -> make_generator() for each non-zero color
                 -> NoiseGenerator.__init__ calibration
           -> StreamingBandLimit(...)              [if any cutoff]
           -> _calibrate_output_filter(...)        [if filter enabled]
           -> StreamingAudioWriter(...)
           -> repeated mixer.generate(frames)
              -> optional output_filter.process(mixed)
              -> apply_master_gain(...)
              -> writer.write(mastered)
           -> writer.close()
        -> mux_black_video(...)                     [video output]
```

The library caller normally enters at `generate_audio()` or constructs a
`NoiseMixer`/generator directly. `noisy.video` is independent of DSP and is
called only after the audio master has been finalized.

## Exact Audio Pipeline

For each retained block, the ordering is exactly:

```text
independent white RNG streams
  -> colored source DSP and fixed source calibration gain
  -> sqrt(power fraction) amplitude gain per source
  -> summation in float64 (NoiseMixer)
  -> optional causal output high-pass, then low-pass (StreamingBandLimit)
  -> fixed master RMS gain and soft-knee peak protection
  -> float32 conversion
  -> PCM-24 FLAC or WAV write
```

Filtering is deliberately after mixing. Filtering each source independently
would be mathematically equivalent for a linear filter in an idealized case,
but would make the combined calibration and state ownership easier to get
wrong and would not represent a single output-band contract. The current code
calibrates the actual mixed stream through the actual output cascade.

Filtering is before mastering because the target RMS describes the delivered
band-limited signal. If mastering preceded filtering, the filter could remove
energy after the target was applied and the final level would depend on the
cutoffs. `_calibrate_output_filter()` therefore warms the filter with a
discarded mixed block and supplies one filtered RMS reference to every later
mastering call.

## Generator State And Calibration

`NoiseGenerator.__init__()` validates a positive integer `sample_rate`, one or
two `channels`, and a positive finite `calibration_seconds`. It creates one
NumPy `default_rng` per channel. A supplied integer seed is expanded through
`np.random.SeedSequence.spawn(channels)`, so stereo channels do not share an
identical random sequence.

Before the generator is usable, it calls `_generate_raw()` for:

```text
calibration_frames = max(4_096, round(sample_rate * calibration_seconds))
```

This block advances all internal DSP and RNG state and is not returned to the
caller. RMS is measured independently per channel over the whole raw block;
`_rms_gain = 1 / calibration_rms` is retained. Later `generate(frames)` calls
advance state, validate shape and finiteness, apply that fixed per-channel gain,
and return `float32`. `generate(0)` returns an empty `(0, channels)` `float32`
array without advancing state.

This is calibration, not normalization: a source's later blocks can have
different measured RMS because noise is random, but the gain cannot pump at
chunk boundaries. The calibration block's final DSP state is intentionally the
initial state of public output.

The source models are:

- `WhiteNoiseGenerator`: independent standard-normal samples.
- `PinkNoiseGenerator`: six parallel one-pole `lfilter_chunk()` branches plus
  direct and B6 terms, using the Paul Kellet-style approximation. Each branch
  has persistent state.
- `BrownNoiseGenerator`: leaky integration followed by a stateful high-pass.
  The leak bounds the random walk; the visible high-pass is separate. The
  default high-pass is 12 Hz and must be below Nyquist.
- `BlueNoiseGenerator`: order-0.5 `fractional_derivative_sos()` when SciPy is
  available, otherwise a finite FIR fractional-difference approximation.
- `VioletNoiseGenerator`: order-1 finite difference with two taps, implemented
  by `StreamingFIR`.

`canonical_color()` accepts case/whitespace-normalized names and the alias
`red -> brown`. `SUPPORTED_COLORS` is the canonical set. Unknown names raise
`ValueError`; mix parsing converts that into `MixError`.

## Mixing Semantics

`parse_mix()` accepts comma-separated `color=ratio` components. Ratios must be
finite and non-negative, and the total must be positive. Canonical duplicates
are combined (`brown=1,red=2` becomes three brown parts); zero-valued sources
are omitted.

`normalize_ratios()` returns normalized power fractions. For normalized
fractions `p_i`, `power_to_amplitude_gains()` returns:

```text
a_i = sqrt(p_i)
```

Thus `brown=70,pink=30` means amplitudes `sqrt(0.7)` and `sqrt(0.3)`. For
independent unit-RMS sources, the expected pre-master RMS is
`sqrt(sum(p_i))`, exposed by `NoiseMixer.expected_rms`; with normalized
ratios this is normally exactly 1.0. `NoiseMixer.generate()` allocates a
float64 zero block, asks every generator for the same frame count, and adds its
fixed amplitude-weighted output.

When a mixer seed is supplied, `_spawn_source_seeds()` creates one child seed
per active color before each generator creates its per-channel RNGs. This
separates source streams while keeping a complete seeded mix reproducible.

## Output Filter API

`StreamingBandLimit(sample_rate, channels, *, highpass_hz=None,
lowpass_hz=None, order=2)` is a causal cascade of zero, one, or two Butterworth
SOS filters. A high-pass is appended first, followed by a low-pass. Each
section is a `StreamingSOS` with state shaped `(sections, 2, channels)`.

Modes are defined by the two optional cutoffs:

| `highpass_hz` | `lowpass_hz` | Behavior |
| --- | --- | --- |
| `None` | `None` | Exact bypass; `process()` returns float64 input values without filter state |
| set | `None` | High-pass only |
| `None` | set | Low-pass only |
| set | set | High-pass then low-pass band-pass behavior |

`process()` requires a two-dimensional `(frames, configured channels)` input and
returns float64. Empty blocks are accepted. Cutoffs must be finite, positive,
and below Nyquist; when both are set, high-pass must be strictly below
low-pass. `order` must be a positive integer. Invalid values raise `ValueError`.

If at least one cutoff is requested and SciPy cannot be imported, construction
raises `RuntimeError("SciPy is required ...")`. A no-cutoff instance remains an
exact bypass without SciPy. This is distinct from the generator fallbacks:
pink and brown use NumPy-compatible stateful paths, and blue falls back from
SOS design to finite FIR when SciPy is unavailable. SciPy is nevertheless a
declared required project dependency for normal operation and for
`StreamingBandLimit` filtering.

### Butterworth SOS And Fractional Design

`StreamingBandLimit` calls `scipy.signal.butter(order, cutoff,
btype=..., fs=sample_rate, output="sos")`. SOS avoids the numerical fragility
of one high-order polynomial representation, and `StreamingSOS` processes each
section with `scipy.signal.sosfilt(..., zi=state)`.

Blue noise uses `fractional_derivative_sos()`, an Oustaloup-style finite causal
approximation to `s**order` over `low_hz..high_hz`. The default range is
`20 Hz..0.45 * sample_rate`; digital limits are prewarped, analog poles and
zeros are generated for `2 * sections + 1` frequencies, and SciPy's
`bilinear_zpk()` and `zpk2sos()` produce the digital SOS cascade. The default
order is 0.5 and default section count is 3. The function validates the range,
returns `None` only when SciPy is absent, and rejects non-finite coefficients.

All causal state is persistent across calls: `lfilter_chunk()` states for
one-pole/IIR stages, FIR history for finite differences, and SOS state for
Butterworth and fractional filters. Consequently a whole-block call and a
series of chunk calls should produce the same samples to the tested floating
point tolerance.

## Calibration, Mastering, And Accounting

With an output filter enabled, `generate_audio()` computes:

```text
calibration_frames = max(4_096, round(sample_rate * calibration_seconds))
calibration = output_filter.process(mixer.generate(calibration_frames))
settled = calibration[calibration_frames // 2:]
expected_mix_rms = sqrt(mean(settled ** 2))
```

The whole calibration block warms the causal output filter, but only its second
half estimates settled-band RMS. The complete block is discarded. A zero,
non-finite, or otherwise unusable reference raises `AudioError`. A requested
recording shorter than this pre-roll still works: the recording starts after
the discarded pre-roll and emits only the requested frames.

Without an output filter, `expected_mix_rms` is `NoiseMixer.expected_rms` and
there is no output-filter pre-roll. Generator calibration still occurred while
constructing each source.

`apply_master_gain()` computes one gain for all blocks:

```text
scaled = mixed * (target_rms_linear / expected_mix_rms)
target_rms_linear = 10 ** (target_rms_db / 20)
```

It then applies a soft knee beginning at `0.90 * peak_ceiling`. Samples above
the threshold are mapped with `tanh` toward, but not beyond, the configured
ceiling. The result is checked against the ceiling with a small numerical
tolerance and converted to `float32`. This is not a hard clip and is not a
look-ahead limiter; large random peaks are softened individually. A zero target
RMS is accepted by the mastering function, while `AudioConfig` rejects target
values above 0 dBFS. Non-finite inputs, invalid target/reference values, and
invalid ceilings raise `AudioError`.

The pipeline's loop is frame-exact:

```text
remaining = duration.frames
while remaining:
    frames = min(config.chunk_frames, remaining)
    ... write(frames) ...
    remaining -= frames
```

Calibration frames are outside this counter. `GenerationStats.frames`, the
writer's `total_frames`, and the file's frame count therefore equal the
sample-accurate requested duration. `calculate_duration()` combines hours,
minutes, and seconds first, rounds once, rejects zero/sub-frame requests, and
guards the signed 64-bit frame limit.

Changing `chunk_seconds` changes block boundaries but not seeded output. The
stateful source, output filter, fixed calibration gains, and fixed master gain
make chunked and whole-block processing deterministic for the same seed and
configuration. The tests assert exact equality for final filtered files and
`allclose` for generator blocks where float conversion/state arithmetic permits
the documented tolerance.

### Memory Bounds

The normal working set is bounded by the largest live block, approximately
`O(chunk_frames * channels)`, plus a fixed amount of filter/source state and
the calibration block. Calibration uses at most
`max(4_096, round(sample_rate * calibration_seconds))` frames. The mixer keeps
one block per source only transiently as each source is added; it does not
accumulate the recording. The SciPy paths operate blockwise. The dependency-free
FIR fallback temporarily combines filter history with the current block, so it
uses `O((chunk_frames + taps) * channels)` for that operation. FLAC/WAV output is
written incrementally. The CLI's `_run()` preflights long WAV requests before
audio generation when estimated PCM-24 size exceeds the classic 4 GiB RIFF
limit. Direct library callers of `generate_audio()` construct and calibrate the
mixer before the writer performs the same validation. In both paths, an
oversized WAV is rejected before any audio blocks are written; FLAC avoids that
specific RIFF guard.

## Public Configuration And CLI Propagation

`AudioConfig` defaults are the library defaults:

| Field | Default |
| --- | ---: |
| `sample_rate` | `48000` |
| `channels` | `1` |
| `chunk_seconds` | `5.0` |
| `target_rms_db` | `-18.0` |
| `peak_ceiling` | `0.98` |
| `brown_highpass_hz` | `12.0` |
| `calibration_seconds` | `4.0` |
| `output_highpass_hz` | `None` |
| `output_lowpass_hz` | `None` |

The library therefore bypasses the output filter unless cutoffs are explicitly
provided. `AudioConfig` validates numeric types (booleans are not accepted as
numbers), positivity, finite values, Nyquist bounds, and high-pass/low-pass
ordering. It is frozen; use a new instance rather than mutating one.

The CLI has the same audio defaults except for output cutoffs. Omitted
`--highpass-hz` resolves to `min(20.0, 0.1 * sample_rate)`, and omitted
`--lowpass-hz` resolves to `min(17000.0, 0.45 * sample_rate)`. Explicit zero
disables that side. `_run()` passes the resolved values into `AudioConfig`,
along with sample rate, channels, chunk size, target RMS, peak ceiling, brown
high-pass, and the seed into `generate_audio()`.

Example library setup:

```python
from noisy.config import AudioConfig, calculate_duration
from noisy.pipeline import generate_audio

config = AudioConfig(
    sample_rate=48_000,
    channels=2,
    chunk_seconds=2.0,
    output_highpass_hz=30.0,
    output_lowpass_hz=16_000.0,
)
duration = calculate_duration(minutes=10, sample_rate=config.sample_rate)
stats = generate_audio(
    power_ratios={"brown": 0.7, "pink": 0.3},
    duration=duration,
    config=config,
    output_path="master.flac",
    seed=7,
    quiet=True,
)
```

Equivalent CLI example:

```bash
noisy --mix brown=70,pink=30 --minutes 10 --channels 2 \
  --highpass-hz 30 --lowpass-hz 16000 --seed 7 \
  --audio-output master.flac
```

The CLI catches `AudioError`, `ConfigurationError`, `MixError`, `VideoError`,
and `ValueError`, reports `error: ...`, and returns status 2. It does not catch
the `RuntimeError` raised when `StreamingBandLimit` filtering is requested
without SciPy, so that missing-dependency failure is not normalized into the
same status-2 `error:` path. Keyboard interrupt returns 130. Output paths must
be distinct; existing outputs require `--force`. `--keep-audio` retains a
stem `.flac` master when only a video output was requested. Without
`--keep-audio`, the audio file used for video muxing is temporary.

## Error Contracts And Numerical Concerns

- `ConfigurationError`: invalid `AudioConfig`, duration, or output-path
  preconditions.
- `MixError`: malformed mix, invalid ratio, invalid seed, or mixer frame count.
- `AudioError`: unsupported audio suffix, unsafe WAV size, missing `soundfile`,
  invalid audio block, unusable filtered calibration, or failed mastering/write.
- `VideoError`: malformed video settings, missing FFmpeg, or mux failure.
- `ValueError`: direct generator/DSP API validation failures and unknown colors.
- `RuntimeError`: invalid generator output shape/RMS/finiteness, or requested
  `StreamingBandLimit` filtering without SciPy.

Use float64 for internal DSP and mixing. Convert to float32 only at generator
boundaries and after mastering. Do not compare long streams with exact equality
unless the test is specifically checking the same operation sequence; use
appropriate tolerances for stateful floating-point DSP. Validate cutoffs
against the selected sample rate, not a fixed 48 kHz assumption. Preserve
independent channel RNGs and persistent filter state, because resetting either
changes the stream at chunk boundaries.

## Extension Points And Invariants

### Adding A Noise Color

1. Add a subclass of `NoiseGenerator` with a canonical `color` and implement
   `_generate_raw(frames)` with shape `(frames, channels)`.
2. Allocate all state in the subclass before `super().__init__()`; base
   construction immediately runs calibration through `_generate_raw()`.
3. Use `_white(frames)` for independent channel white excitation when suitable.
4. Retain every IIR/FIR/SOS state between calls and return finite float64 raw
   samples. The base class handles fixed RMS calibration and float32 output.
5. Add the class to `_GENERATOR_TYPES` and, if it is public, update
   `SUPPORTED_COLORS` and exports.
6. Add deterministic chunk-continuity and spectral-slope tests; test aliases
   only if the alias is intentional.

Do not calibrate inside every `generate()` call, reset random generators at
block boundaries, or normalize each block. Those changes break level
stability and seeded chunk equivalence.

### Adding A DSP Stage

Keep a stage causal and stateful, expose a clear `(frames, channels)` contract,
validate finite coefficients and dimensions at construction, and put its state
in an object rather than module globals. Decide explicitly whether the stage
belongs in a source model, the mixed output path, or mastering. For an output
stage that changes RMS, include it in `_calibrate_output_filter()` or provide
an equivalent single pre-roll/reference mechanism. Preserve the current
ordering unless the level contract and tests are deliberately redesigned.

For a new IIR design prefer SOS coefficients and `StreamingSOS`; for a finite
impulse response use `StreamingFIR`. If SciPy is optional for the new stage,
document whether it has a meaningful fallback, and make the no-dependency
behavior explicit rather than silently changing filter semantics.

### What Not To Change Casually

- Do not move output filtering before `NoiseMixer.generate()` or after
  `apply_master_gain()` without revisiting calibration and target-level meaning.
- Do not replace power-to-amplitude square roots with direct ratio weights.
- Do not discard or reset state at chunk boundaries.
- Do not count calibration frames in `GenerationStats` or the output duration.
- Do not remove the peak-ceiling defensive check or permit non-finite samples to
  reach `soundfile`.
- Do not make long recordings depend on a full-duration NumPy array.
- Do not make CLI defaults silently become library defaults; their current
  difference is intentional and tested.
- Do not assume the build/distribution copies are authoritative; edit `noisy/`
  sources and regenerate artifacts only in a release workflow.

## Tests And Verification Matrix

Run the complete suite from the repository root:

```bash
.venv/bin/python -m pytest
```

Focused checks:

| Area | Command | What it verifies |
| --- | --- | --- |
| Generators and mixer | `.venv/bin/python -m pytest tests/test_generators.py tests/test_mixer.py` | Seeds, independent channels, state continuity, aliases, power ratios, parse errors |
| DSP API | `.venv/bin/python -m pytest tests/test_dsp.py` | Validation, SciPy requirement, bypass, one/two-sided filtering, chunk equivalence, attenuation, shapes |
| Spectral models | `.venv/bin/python -m pytest tests/test_spectral.py` | Approximate 0/-3/-6/+3/+6 dB-per-octave slopes |
| Pipeline filter | `.venv/bin/python -m pytest tests/test_pipeline_filter.py` | Post-mix filtering, RMS target, short calibration, chunk-size determinism, stereo, frame counts |
| Config/audio | `.venv/bin/python -m pytest tests/test_config_audio.py` | Duration rounding, config contracts, fixed mastering, ceiling, writer counts, RIFF guard |
| CLI | `.venv/bin/python -m pytest tests/test_cli.py` | CLI cutoff defaults, zero disables, and propagation into `AudioConfig` |
| Video command | `.venv/bin/python -m pytest tests/test_video.py` | Black source, codec/container defaults, resolution parsing |

For a small manual smoke run that exercises the real writer:

```bash
.venv/bin/noisy --mix white=100 --seconds 0.25 \
  --sample-rate 8000 --highpass-hz 0 --lowpass-hz 0 \
  --audio-output /tmp/noisy-smoke.flac --seed 1 --quiet
```

Then inspect frame count and finiteness with `soundfile` or rerun the focused
pipeline tests. When changing a filter or generator, include both a single
large call and differently split calls with the same seed, check stereo shape,
check finite output and peak ceiling, and test a requested duration shorter
than calibration. For spectral work, use the established Welch test band and
allow tolerance for finite-length estimates and the approximation models.

## Maintenance And Debugging Workflow

When output is wrong, first identify which invariant failed:

1. Check `Duration.frames`, `GenerationStats.frames`, and file metadata for
   frame accounting.
2. Compare the same seed with one generator/mixer call versus multiple chunks.
3. Inspect source `rms_gain`, `NoiseMixer.amplitude_gains`, and
   `expected_rms` before blaming mastering.
4. If filtering is enabled, verify the resolved cutoffs, SciPy availability,
   SOS finiteness, and that the discarded calibration path consumes both mixer
   and filter state.
5. Measure final RMS after the filter and mastering, not before it; inspect
   maximum absolute sample value separately for ceiling behavior.
6. For NaN/Inf failures, follow the contract boundary: generator raw output,
   filter output, mastering input, then writer input.

When adding a stage, test it in isolation first, then test its exact location
in the pipeline and its effect on calibration. Keep changes small because the
current tests intentionally encode ordering, state persistence, frame
accounting, and CLI/library default differences.
