# Changelog

All notable changes to this project are documented here.

## [0.1.0] - 2026-08-16

Initial public release.

- Streams bounded-memory white, pink, brown, blue, and violet noise generation.
- Supports power-ratio mixes of noise colors.
- Provides stateful generators with reproducible seeds.
- Applies streaming output high-pass and low-pass filters, fixed RMS mastering, and a peak ceiling.
- Writes PCM-24 FLAC/WAV output and guards WAV files against the 4 GiB limit.
- Optionally muxes black-screen video with FFmpeg: MP4 H.264/AAC with `+faststart` or MKV H.264/FLAC.
- Offers both a CLI and library API, with a test suite covering core behavior.
- Includes YouTube metadata and upload/QA guides.
