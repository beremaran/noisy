# YouTube Long-Form Colored-Noise Master Guide

This is operational guidance for generating, checking, and uploading long
colored-noise black-screen videos with `noisy`. It is not a guarantee of
YouTube acceptance, processing, availability, monetisation, or playback
quality. YouTube changes its requirements and transcodes uploads; verify the
current requirements in YouTube Studio and the [official upload
help](https://support.google.com/youtube/answer/57407) before delivery.

The generator does not upload or publish media. Keep a lossless local master,
record the complete command and seed, and treat the uploaded video as a
platform delivery copy.

## Current Signal Path

The current CLI path is:

```text
colored-noise sources, optionally seeded
  -> per-source calibration and power-ratio mix
  -> causal output high-pass, then low-pass (post-mix)
  -> discarded filter calibration/pre-roll
  -> one fixed RMS master gain
  -> soft-knee peak ceiling
  -> streaming PCM-24 FLAC or WAV writer
  -> optional FFmpeg black-video mux
```

Filtering is deliberately **after mixing and before mastering**. The final
file therefore has one shared output band, and the fixed master gain is based
on the filtered signal. The causal filter retains state across chunks. A
discarded calibration block warms that state and prevents filter startup
transients from becoming the beginning of the recording.

This post-mix high/low filtering is why the default output is less harsh than
an unbounded white, blue, or violet source: excessive top-end energy is
limited before the level is mastered. The high-pass also removes subsonic
rumble from the delivered mix. It is tone shaping, not a promise that every
speaker, headphone, or listener will find a setting comfortable.

Brown has a separate internal drift-control high-pass. It defaults to 12 Hz
and controls brown's leaky-integrator drift, DC, and subsonic energy. It is
not the final mix high-pass and is not disabled by `--highpass-hz 0`.

For implementation detail, see the [user guide](./USER-GUIDE.md) and
[developer guide](./DEVELOPER-GUIDE.md). The [README](../README.md) has a
short command overview.

## Defaults And Filter Choices

CLI defaults are intended for 48 kHz masters:

| Setting | Default | Meaning |
| --- | ---: | --- |
| Sample rate | `48000` Hz | Output sample rate; mono is the default. |
| Output high-pass | `20` Hz | Final mix-wide high-pass. |
| Output low-pass | `17000` Hz | Final mix-wide low-pass. |
| Target RMS | `-18` dBFS | One fixed master target, not per-chunk normalization. |
| Peak ceiling | `0.98` linear | About `-0.18` dBFS maximum; soft-knee protection. |
| Chunk duration | `5` seconds | Streaming block size, not an audible loop. |
| Brown source high-pass | `12` Hz | Brown-only drift-control filter. |
| Calibration | nominally `4` seconds | Source calibration and, when filtered, discarded output pre-roll; the implementation uses `max(4096, round(sample_rate * calibration_seconds))` frames, so the actual pre-roll can be longer. |

### Sample-rate-safe behavior

When a CLI output cutoff is omitted, the effective value is:

| Option | Resolution rule | At 48 kHz |
| --- | --- | ---: |
| `--highpass-hz` | `min(20, 0.1 * sample_rate)` | `20` Hz |
| `--lowpass-hz` | `min(17000, 0.45 * sample_rate)` | `17000` Hz |

The low-pass fallback is below Nyquist at lower sample rates. For example, at
16 kHz the omitted low-pass is 7,200 Hz, not 17 kHz. Any explicit nonzero
cutoff must be finite and below Nyquist; a high-pass must also be below the
low-pass.

In the CLI, explicit `0` means **disable that side** of the final output
filter:

```bash
# Disable only the final output high-pass; keep the low-pass.
uv run noisy --mix pink=100 --hours 1 --highpass-hz 0 --audio-output pink.flac

# Disable both final output cutoffs.
uv run noisy --mix brown=100 --hours 1 \
  --highpass-hz 0 --lowpass-hz 0 --audio-output brown-wide.flac
```

Disabling output filtering does not disable brown's internal 12 Hz filter.
This `0` convention is CLI-only: direct `AudioConfig` library fields use
`None` to bypass and reject zero as a cutoff.

### Practical starting points

Start with the defaults, then make a short test and listen at a comfortable
low level. The following are deliberately conservative alternatives:

| Case | Suggested command options | Reason |
| --- | --- | --- |
| Brown | `--highpass-hz 20 --lowpass-hz 10000` | Reduces rumble and leaves a deep but less muddy band. |
| Pink | `--highpass-hz 20 --lowpass-hz 16000` | Retains more air than brown while avoiding the full top end. |
| White | `--highpass-hz 20 --lowpass-hz 12000` | White has equal power per Hz and can sound hissy at the top. |
| Blue | `--highpass-hz 20 --lowpass-hz 9000` | Blue is upper-frequency weighted. |
| Violet | `--highpass-hz 20 --lowpass-hz 7000` | Violet is the brightest listed color; begin narrow. |

Soften white, blue, violet, or a bright blend by lowering the low-pass. Raise
it when the result sounds dull and you have checked it at low playback level.
Raise the high-pass only when rumble or excessive low-frequency weight is
audible; too much can make brown thin. Disable a cutoff only for a deliberate
wide-band experiment or when an external delivery specification requires it,
not as a way to recover a harsh master after the fact.

Mix values are approximate **power** contributions, not amplitude percentages:
`brown=70,pink=30` gives square-root amplitude gains for approximately 70/30
power. Supported colors are `white`, `pink`, `brown` (also `red`), `blue`, and
`violet`.

## Recommended Generation Commands

Install the project with `uv sync`. Use `uv run noisy --help` to confirm the
installed CLI. FFmpeg is required only when `--video-output` is requested and
must be available as `ffmpeg` on `PATH`.

Run a short smoke test before a multi-hour job:

```bash
uv run noisy --mix brown=70,pink=30 --seconds 10 \
  --sample-rate 48000 --channels 1 --seed 4101 \
  --audio-output smoke.flac
```

### Brown or pink

This is a practical 10-hour brown/pink master. The default 20 Hz/17 kHz
post-mix band is used here; set explicit cutoffs if you want the recipe to
document its tonal contract permanently.

```bash
uv run noisy \
  --mix brown=70,pink=30 \
  --hours 10 --sample-rate 48000 --channels 1 \
  --target-rms-db -18 --peak-ceiling 0.98 \
  --seed 4101 --chunk-seconds 300 \
  --audio-output brown-pink-10h.flac \
  --video-output brown-pink-10h.mp4
```

For a deeper brown-first version, use `--lowpass-hz 10000`. Keep
`--brown-highpass-hz 12` unless you specifically need to change brown's
source drift behavior.

### White

Use a lower output low-pass for a less piercing white-noise delivery:

```bash
uv run noisy \
  --mix white=100 --hours 10 \
  --sample-rate 48000 --channels 1 \
  --highpass-hz 20 --lowpass-hz 12000 \
  --target-rms-db -18 --peak-ceiling 0.98 \
  --seed 4201 --chunk-seconds 300 \
  --audio-output white-10h.flac \
  --video-output white-10h.mp4
```

### Blue or violet

Treat these as bright experiments. Start with a short file and listen before
spending hours generating it:

```bash
uv run noisy \
  --mix blue=50,violet=50 --hours 1 \
  --sample-rate 48000 --channels 1 \
  --highpass-hz 20 --lowpass-hz 7000 \
  --target-rms-db -18 --peak-ceiling 0.98 \
  --seed 4301 --chunk-seconds 300 \
  --audio-output blue-violet-1h.flac \
  --video-output blue-violet-1h.mp4
```

If it is too dull, try 9 or 12 kHz before returning to the 17 kHz default.
Do not describe a result as smoothed, remastered, or wide-band unless that is
what the command actually produced.

## Level, Reproducibility, And Duration

- `--target-rms-db -18` is the default fixed RMS target in dBFS. It is based on
  the filtered signal and is not recalculated for each chunk, so short windows
  can measure above or below it.
- `--peak-ceiling 0.98` applies a smooth soft-knee curve from 90% of the
  ceiling. It is peak protection, not a hard clip or per-block limiter.
- The output filter's calibration pre-roll is discarded. The final file
  contains only the requested duration, not the calibration frames.
- The nominal output calibration is 4 seconds, but the implementation uses
  `max(4096, round(sample_rate * calibration_seconds))` frames. At ordinary
  sample rates this is usually 4 seconds; at lower rates the 4,096-frame
  minimum can make it longer. This pre-roll is never included in the file's
  requested duration.
- `--seed` accepts an integer and makes the source/channel random streams
  reproducible when mix, rate, channels, filters, duration, and package version
  are held constant. Record the full command and version.
- `--chunk-seconds` controls bounded-memory streaming. Stateful generators and
  filters retain history, so changing chunk size does not intentionally reset
  the sound or create loops. The default is 5 seconds; 300 seconds can improve
  throughput at the cost of memory.
- `--hours`, `--minutes`, and `--seconds` are combined and rounded once to an
  integer frame count. This makes duration sample-accurate rather than allowing
  floating-point drift at chunk boundaries.
- Defaults are 48 kHz and mono. Use `--channels 2` only when stereo is wanted;
  the channels use independent random streams.

## Format And Container Choices

| Deliverable | Command/output | Use |
| --- | --- | --- |
| Retained audio master | `--audio-output master.flac` | Preferred long-term local master: PCM-24 in FLAC, without classic WAV's 4 GiB RIFF limit. |
| WAV test/interchange | `--audio-output test.wav` | PCM-24 and convenient for tools, but long recordings can exceed the classic 4 GiB limit; this build does not enable RF64. |
| YouTube upload candidate | `--video-output final-upload.mp4` | Use MP4 as the explicit upload candidate: H.264, pure black `1920x1080` by default, AAC at 256 kbps, and `+faststart`. Verify current YouTube requirements before upload. |
| Local A/V copy with lossless audio track | `--video-output archive.mkv` | Local archival/interchange copy with H.264 video and FLAC audio by default. The H.264 video remains lossy, and MKV/FLAC is not presented here as a YouTube upload format. |

Always specify `--audio-output` when you want to retain the audio master. If
only video output is supplied, the intermediate audio is temporary. Add
`--keep-audio` to retain a `.flac` beside the video.

AAC is the MP4 default for compatibility, but it is lossy and FFmpeg/container
support must agree with any `--audio-codec` override. FLAC is lossless locally
and is the MKV default. Keep MKV/FLAC as a local archival or interchange copy;
use the MP4/AAC file as the upload candidate and verify current platform
requirements. YouTube will transcode a supported upload; a local FLAC track
does not guarantee lossless published playback.

The black video defaults to 1 fps because every frame is identical. Use
`--fps 30` if a downstream workflow expects a conventional frame rate. The
finite audio duration is authoritative; the mux path avoids truncating audio
merely because the synthetic video uses a low frame rate.

## Local Pre-Upload QA

Run checks against the **final file you intend to upload**, and separately keep
the `.flac` local master. Replace the path below with the actual file.

### Container, duration, rate, and channels

```bash
ffprobe -v error \
  -show_entries format=duration,size,format_name:stream=index,codec_type,codec_name,codec_long_name,width,height,r_frame_rate,sample_rate,channels,channel_layout,duration \
  -of default=noprint_wrappers=1 \
  final-upload.mp4
```

Confirm the expected duration (for example, 36,000 seconds for 10 hours),
48,000 Hz, the intended channel count, a video stream if applicable, and the
intended codecs/container. Do not infer exact duration from the number of
video frames alone.

### Audio finiteness, frames, RMS, and peaks

For a FLAC or WAV file, this repository's Python environment can check the
decoded samples without loading a long file into memory:

```bash
uv run python -c 'import sys, numpy as np, soundfile as sf; p=sys.argv[1]; f=sf.SoundFile(p); n=0; ss=0.0; peak=0.0; finite=True; block=sf.blocks(p, blocksize=48000, dtype="float64", always_2d=True); [(globals().__setitem__("n", n + len(x)), globals().__setitem__("ss", ss + float(np.square(x).sum())), globals().__setitem__("peak", max(peak, float(np.abs(x).max()))), globals().__setitem__("finite", finite and bool(np.isfinite(x).all()))) for x in block]; print({"frames": n, "sample_rate": f.samplerate, "channels": f.channels, "duration": n / f.samplerate, "rms_dbfs": 20 * np.log10(np.sqrt(ss / (n * f.channels))), "peak": peak, "finite": finite})' master.flac
```

Check that `finite` is `True`, frames equal the requested sample-accurate
count, RMS is near the configured target over a long enough window, and peak
is no greater than the configured ceiling (allowing tiny inspection/tool
rounding differences). If the one-liner is awkward in a shell, use `ffprobe`
for metadata and a trusted audio editor or analysis tool for samples.

### Listening and mux checks

- Listen to the beginning, a middle segment, and the end at low volume on
  headphones and speakers.
- Check for clicks, level jumps, filter startup artifacts, excessive hiss,
  rumble, or a tonal change that makes the file tiring.
- Confirm the filename, mix, duration, seed, filter settings, and title all
  agree. Use stable names such as `brown-pink-20hz-10khz-10h-seed4101.flac`.
- Play or seek through the final MP4/MKV and verify the black picture and audio
  continue together to the end.
- If available, run `ffmpeg -v error -i final-upload.mp4 -f null -` to
  decode-check the muxed file. Substitute the actual final MP4 path for
  `final-upload.mp4`; a nonzero exit status is a delivery blocker.
- Retain the lossless FLAC, command line, package version, `ffprobe` output,
  and listening notes even after creating the upload copy.

## Upload Workflow And Metadata

1. Finish and QA the local FLAC master and final MP4/MKV. Do not upload an
   unlistened multi-hour render.
2. In YouTube Studio, create the upload, select the final MP4 delivery file,
   and wait for processing/checks to complete. Verify the current Studio
   prompts, the [official upload troubleshooting
   guide](https://support.google.com/youtube/troubleshooter/2888402), and
   [YouTube's recommended upload encoding
   settings](https://support.google.com/youtube/answer/1722171).
3. Use an accurate title, description, thumbnail, playlist, audience setting,
   visibility, and interruption/monetisation settings. These are channel and
   account decisions, not properties guaranteed by this generator.
4. Preview the processed result at the beginning, middle, and end, then inspect
   playback on more than one device before making it public.
5. Record the uploaded URL, upload date, source filename, and any processing or
   loudness observations alongside the local master.

### Title examples

```text
Brown Noise Black Screen | Sleep, Study, Focus | 10 Hours
Pink Noise Black Screen | Sleep, Focus, Study | 10 Hours
White Noise Black Screen | Sleep, Study, Focus | 10 Hours
Blue + Violet Noise Black Screen | 1 Hour | 7 kHz Low-Pass Test
```

Put the actual noise color/mix and duration near the front. Avoid medical,
therapeutic, or unsupported claims, and do not claim `No ads` unless the
channel's actual settings and viewing context support that statement.

### Description example

```text
10 hours of continuous brown and pink noise with a completely black screen.
Use it as a low-distraction background for sleep, study, focus, relaxation, or
masking everyday background sound.

Mix: brown=70,pink=30
Duration: 10:00:00
Output band: 20 Hz high-pass, 17 kHz low-pass
Screen: pure black for the full video

The audio is a generated mono noise stream, not a field recording. Use a
comfortable low volume and stop playback if the sound is uncomfortable. This
video is general background listening, not medical advice or treatment.
```

Change the colors, duration, and output band to match the command exactly.
Tags should be a small, relevant set, for example:

```text
brown noise, brown noise black screen, brown noise 10 hours, sleep noise, study noise, noise masking
```

Useful official references are [How YouTube search
works](https://support.google.com/youtube/answer/16090438), [Tips for video
descriptions](https://support.google.com/youtube/answer/12948449), and [Add tags
to your YouTube videos](https://support.google.com/youtube/answer/146402).
These explain product features and metadata guidance; they do not guarantee
ranking, audience retention, or acceptance of a particular upload.

## Troubleshooting

| Symptom | Remedy |
| --- | --- |
| `Cutoff must be below Nyquist` | Lower the explicit cutoff for the selected sample rate. Remember that Nyquist is half the sample rate. At 16 kHz, the omitted CLI low-pass resolves to 7.2 kHz. |
| `High-pass must be below low-pass` | Lower the high-pass or raise the low-pass. `0` disables a CLI output side; it is not a direct library cutoff. |
| Brown still has low-frequency weight | The final `--highpass-hz` and brown's separate `--brown-highpass-hz` do different jobs. Raise the final high-pass for delivery rumble, or change the brown setting only when changing the source model is intentional. |
| White/blue/violet sounds harsh | Lower `--lowpass-hz`, reduce the bright component's power ratio, and re-listen at low volume. The default post-mix filter is already intended to reduce excess top-end energy. |
| Brown sounds thin | Lower the final high-pass or use a higher low-pass; do not assume the brown source high-pass is the final output filter. |
| WAV size error or a WAV near 4 GiB | Use `.flac`. This build writes PCM-24 WAV and rejects estimates beyond the classic 4 GiB RIFF limit; RF64 is not enabled. |
| `FFmpeg not found` | Install FFmpeg and verify `ffmpeg -version` succeeds on `PATH`. Audio-only FLAC/WAV generation does not need FFmpeg. |
| FFmpeg mux fails or codec/container is rejected | Confirm the input decodes, output is `.mp4` or `.mkv`, and the selected audio codec is supported by both FFmpeg and that container. Try the defaults, or use MP4/AAC for compatibility and MKV/FLAC for the local lossless A/V copy. |
| Final video is shorter or audio is missing | Decode with `ffmpeg -v error -i file -f null -`, inspect both streams with `ffprobe`, and remux from the retained FLAC. Do not rely on video frame count at 1 fps to measure audio duration. |
| Uploaded playback is quieter, brighter, or otherwise different | Compare the processed upload with the local delivery file at matched playback level. YouTube transcoding and player/device volume processing can change the result; verify current platform behavior rather than targeting an asserted universal loudness rule. |
| RMS is not exactly `-18` in a short measurement | The target is one fixed stream gain based on calibration, not block normalization. Measure a sufficiently long segment and inspect peak separately. |

For the complete validation contract, duration rules, DSP details, and focused
tests, use [`docs/USER-GUIDE.md`](./USER-GUIDE.md) and
[`docs/DEVELOPER-GUIDE.md`](./DEVELOPER-GUIDE.md).
