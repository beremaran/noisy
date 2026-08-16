# Contributing

## Development setup

Install [uv](https://docs.astral.sh/uv/), use Python >= 3.10, and install
FFmpeg only if you plan to use `--video-output`. Then install the test extra:

```bash
uv sync --extra test
```

## Running tests

```bash
uv run pytest
uv run noisy --help
```

## Project layout

- `noisy/` - The Python package.
- `tests/` - The test suite.
- `docs/` - `USER-GUIDE.md` is the operational reference, `DEVELOPER-GUIDE.md` is the implementation reference, `youtube-upload-guide.md` is the YouTube metadata guide, and `youtube-master-guide.md` is the upload/QA master guide.
- `scripts/generate-youtube.sh` - Regenerates the published YouTube videos into `outputs/youtube/`.

## Workflow

Report issues with the full command line used. For code changes, fork the
repository, create a branch, make small focused commits, and open a PR that
describes what changed and why. Keep the relevant docs in sync with behavior
changes, and make sure the test suite passes.

## Code style

Follow the existing style in the package. Keep public API and CLI behavior
covered by tests in `tests/`.
