#!/usr/bin/env bash
# Build and install Second Brain from this checkout.
set -euo pipefail

die() { printf 'Second Brain: %s\n' "$*" >&2; exit 1; }
[[ $# -le 1 ]] || die 'Usage: ./install.sh [applications-directory]'
[[ $(uname -sm) == 'Darwin arm64' ]] || die 'An Apple Silicon Mac is required.'
[[ $EUID != 0 ]] || die 'Run the installer as your normal user, not with sudo.'
command -v uv >/dev/null || die 'Install uv from https://docs.astral.sh/uv/ and try again.'
xcrun --find swift >/dev/null || die 'Install Xcode Command Line Tools with xcode-select --install.'

directory=$(cd -- "${1:-/Applications}" && pwd -P)
name='Second Brain.app'
destination="$directory/$name"
mode=new
if [[ -e "$destination" || -L "$destination" ]]; then
    [[ ! -L "$destination" && $(plutil -extract CFBundleIdentifier raw "$destination/Contents/Info.plist") == com.secondbrain.menubar ]] || die "Refusing to replace an unrelated app or link at $destination."
    if lsof -t "$destination/Contents/MacOS/SecondBrainBar" >/dev/null; then
        die 'Quit Second Brain from its menu bar and run the installer again.'
    elif [[ $? != 1 ]]; then
        die 'Could not check whether Second Brain is running.'
    fi
    mode=replace
fi
support="$HOME/Library/Application Support/Second Brain"
backend="$support/backend"
mkdir -p "$support"
stage= work=
shlock -p $$ -f "$support/install.lock" || die 'Another installation is in progress.'
trap 'rm -rf ${stage:+"$stage"} ${work:+"$work"}; rm -f "$support/install.lock"' EXIT
backend_mode=new
if [[ -e "$backend" || -L "$backend" ]]; then
    [[ ! -L "$backend" && -f "$backend/pyvenv.cfg" ]] || die "Refusing to replace an unrelated directory or link at $backend."
    backend_mode=replace
fi
stage=$(mktemp -d "$directory/.second-brain-install.XXXXXXXX") || die "Cannot write to $directory."
work=$(mktemp -d "$support/.install.XXXXXXXX")
cd "$(dirname "$0")"
./gui/SecondBrainBar/bundle.sh "$stage/$name"

# Stage on the destination volume. Relocatable entry points still work after the rename.
venv="$work/backend"
uv venv --relocatable --managed-python --python 3.11 "$venv"
UV_PROJECT_ENVIRONMENT="$venv" uv sync --locked --no-dev --no-editable --reinstall-package second-brain
helper=$("$venv/bin/python" -I -c 'from pathlib import Path; from second_brain.cloud_llm import pi; print(Path(pi.__file__).parent)')
./scripts/setup-pi.sh "$venv/bin" "$helper"

publish() {
    /usr/bin/osascript -l JavaScript - "$@" <<'JS'
function run([source, target, mode]) {
    ObjC.bindFunction('renamex_np', ['int', ['char *', 'char *', 'unsigned int']]);
    const flag = mode === 'replace' ? 0x2 : 0x4; // RENAME_SWAP or RENAME_EXCL
    if ($.renamex_np(source, target, flag) !== 0) throw Error('Could not install ' + target);
}
JS
}
publish "$venv" "$backend" "$backend_mode"
if ! publish "$stage/$name" "$destination" "$mode"; then
    publish "$backend" "$venv" "$backend_mode"
    die 'Could not install the app; the previous backend was restored.'
fi
printf 'Installed %s.\n' "$destination"
