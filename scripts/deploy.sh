#!/usr/bin/env bash
# WSL front-end for deploy.ps1: the repo lives on the Linux side now, but the HA
# config share is SMB and only reachable through Windows.
#
#   scripts/deploy.sh            # one-shot mirror, then restart HA yourself
#   scripts/deploy.sh --watch    # re-mirror on every save
#   scripts/deploy.sh --restart  # mirror + restart HA over the API (needs HA_TOKEN in .env)
#
# Two things the move to \\wsl.localhost\ broke, both handled here:
#   - a UNC path is an untrusted zone, so `pwsh -File` on it trips the RemoteSigned
#     execution policy; we pass -ExecutionPolicy Bypass.
#   - FileSystemWatcher gets no change notifications over the 9p bridge, so -Watch
#     never fires. We poll from the Linux side instead and shell out per change.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/custom_components/tcl_lyon"

watch=0
ps_args=()
for arg in "$@"; do
    case "$arg" in
        --watch)   watch=1 ;;
        --restart) ps_args+=("-Restart") ;;
        *) echo "unknown option: $arg (expected --watch and/or --restart)" >&2; exit 2 ;;
    esac
done

pwsh_exe=""
for candidate in \
    "/mnt/c/Program Files/PowerShell/7/pwsh.exe" \
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"; do
    [[ -x "$candidate" ]] && { pwsh_exe="$candidate"; break; }
done
if [[ -z "$pwsh_exe" ]]; then
    echo "no Windows PowerShell found — is WSL interop enabled?" >&2
    exit 1
fi

script_win="$(wslpath -w "$REPO_ROOT/scripts/deploy.ps1")"

mirror() {
    "$pwsh_exe" -NoProfile -ExecutionPolicy Bypass -File "$script_win" "${ps_args[@]}"
}

# mtimes of every source file, hashed — cheap enough at ~24 files and needs no inotify.
fingerprint() {
    find "$SOURCE_DIR" -type f ! -name '*.pyc' ! -name '*.pyo' -not -path '*/__pycache__/*' \
        -printf '%T@ %p\n' | sort | sha256sum
}

mirror
[[ $watch -eq 0 ]] && exit 0

echo "Watching $SOURCE_DIR for changes (Ctrl+C to stop)..."
last="$(fingerprint)"
while true; do
    sleep 1
    current="$(fingerprint)"
    if [[ "$current" != "$last" ]]; then
        sleep 0.25  # let a burst of saves settle
        current="$(fingerprint)"
        mirror
        last="$current"
    fi
done
