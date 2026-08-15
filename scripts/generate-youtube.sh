#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/generate-youtube.sh [--force] [--dry-run]

Generate the documented YouTube-ready noise videos in outputs/youtube/.

Options:
  --force    overwrite existing video files
  --dry-run  print the commands without running them
  --help     show this help
EOF
}

force=false
dry_run=false
for arg in "$@"; do
    case "$arg" in
        --force) force=true ;;
        --dry-run) dry_run=true ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            printf 'Error: unknown argument: %s\n\n' "$arg" >&2
            usage >&2
            exit 2
            ;;
    esac
done

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
OUTPUT_DIR="$REPO_ROOT/outputs/youtube"

if ! command -v uv >/dev/null 2>&1; then
    printf 'Error: uv is required but was not found on PATH. Install uv and retry.\n' >&2
    exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
    printf 'Error: ffmpeg is required but was not found on PATH. Install ffmpeg and retry.\n' >&2
    exit 1
fi

if [[ "$dry_run" == false ]]; then
    mkdir -p "$OUTPUT_DIR"
fi

recipes=(
    'white=100|10|60|10000|4101|soft-white-60hz-10khz-10h-seed4101.mp4'
    'pink=100|10|40|14000|4201|sleep-pink-40hz-14khz-10h-seed4201.mp4'
    'pink=70,brown=30|10|40|10000|4301|warm-pink-40hz-10khz-10h-seed4301.mp4'
    'brown=100|10|30|12000|4401|brown-30hz-12khz-10h-seed4401.mp4'
    'pink=50,brown=50|10|30|12000|4501|pink-brown-30hz-12khz-10h-seed4501.mp4'
)

if [[ "$force" == false ]]; then
    for recipe in "${recipes[@]}"; do
        IFS='|' read -r _mix _hours _highpass _lowpass _seed filename <<<"$recipe"
        output="$OUTPUT_DIR/$filename"
        if [[ -e "$output" ]]; then
            printf 'Error: output already exists: %s (pass --force to overwrite)\n' "$output" >&2
            exit 1
        fi
    done
fi

cd "$REPO_ROOT"
completed=0
for recipe in "${recipes[@]}"; do
    IFS='|' read -r mix hours highpass lowpass seed filename <<<"$recipe"
    output="$OUTPUT_DIR/$filename"
    command=(
        uv run noisy
        --mix "$mix"
        --hours "$hours"
        --sample-rate 48000
        --channels 1
        --highpass-hz "$highpass"
        --lowpass-hz "$lowpass"
        --target-rms-db -18
        --peak-ceiling 0.98
        --seed "$seed"
        --chunk-seconds 300
        --video-output "$output"
    )
    if [[ "$force" == true ]]; then
        command+=(--force)
    fi

    if [[ "$dry_run" == true ]]; then
        printf 'dry-run:'
        printf ' %q' "${command[@]}"
        printf '\n'
    else
        "${command[@]}"
        completed=$((completed + 1))
    fi
done

if [[ "$dry_run" == true ]]; then
    printf 'Dry run complete: %d video commands; no media generated.\n' "${#recipes[@]}"
else
    printf 'Completed: %d YouTube-ready videos in %s.\n' "$completed" "$OUTPUT_DIR"
fi
