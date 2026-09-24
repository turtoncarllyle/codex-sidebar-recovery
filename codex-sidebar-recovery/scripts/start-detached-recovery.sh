#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This launcher supports macOS only." >&2
    exit 1
fi

if [ "$#" -lt 3 ]; then
    echo "Usage: $0 CODEX_HOME OUTPUT_DIRECTORY PYTHON [--host-key HOST_KEY]" >&2
    exit 2
fi

codex_home=$1
output_directory=$2
python_path=$3
shift 3

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
recovery_script=$script_dir/recover_sidebar.py
label="codex-sidebar-recovery-$(date +%Y%m%d-%H%M%S)-$$"

mkdir -p "$output_directory"
launchctl submit -l "$label" -- "$python_path" -B "$recovery_script" \
    --codex-home "$codex_home" --output-dir "$output_directory" --wait "$@"
printf '%s\n' "$label" > "$output_directory/launch-label"

cat <<EOF
Launch label: $label
Status path: $output_directory/status.json
Verify job: launchctl print gui/$(id -u)/$label
Verify process: ps -axo pid=,ppid=,comm= | grep -E '[C]odex|[C]hatGPT'
EOF
