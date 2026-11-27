#!/bin/bash
# Install private Node beside Python and the locked packages beside the JS helper.
set -euo pipefail
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
unset CDPATH
umask 022

usage='Usage: scripts/setup-pi.sh <bin-directory> <helper-directory>'
if [[ $# == 1 && $1 == --help ]]; then echo "$usage"; exit 0; fi
if [[ $# != 2 || -z $1 || -z $2 ]]; then echo "$usage" >&2; exit 2; fi
die() { printf 'setup-pi: %s\n' "$*" >&2; exit 1; }
[[ $(uname -sm) == 'Darwin arm64' ]] || die 'requires a native macOS arm64 build host'

# From https://nodejs.org/dist/v22.23.5/SHASUMS256.txt.asc, signed by Node releaser
# Antoine du Hamel (5BE8A3F6C8A5C01D106C0AD820B1A390B168D356). Reverify on updates.
version=v22.23.5
dist=node-$version-darwin-arm64
sha256=d0c68c321161bb2c6483efea9a343010dea8e5e7f60d73c6a7eba140016d662f

helper=$(cd -- "$2" && pwd -P)
for file in package.json package-lock.json pi-helper.mjs; do
    [[ -f "$helper/$file" ]] || die "missing $helper/$file"
done
modules=$helper/node_modules
[[ ! -L $modules && ( ! -e $modules || -d $modules ) ]] || die "not a local directory: $modules"
mkdir -p -- "$1"
bin=$(cd -- "$1" && pwd -P)
[[ $bin/ != "$modules/"* ]] || die 'bin-directory must be outside the managed node_modules tree'

# npm ci deletes its target tree. Stage and probe a replacement on the same volume first.
stage=$(mktemp -d "$helper/.setup-pi.XXXXXX")
tools= download=
trap 'rm -rf "$stage" ${tools:+"$tools"} ${download:+"$download"}' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

cache=${XDG_CACHE_HOME:-$HOME/Library/Caches}/second-brain/node
mkdir -p "$cache"
cache=$(cd -- "$cache" && pwd -P)
archive=$cache/$dist.tar.xz
verify_archive() {
    local sum
    sum=$(shasum -a 256 < "$1")
    [[ ${sum%% *} == "$sha256" ]] || die "SHA-256 mismatch: $1 (do not use this archive)"
}
if [[ ! -f $archive ]]; then
    download=$(mktemp -d "$cache/.download.XXXXXX")
    curl -q --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --retry 2 --connect-timeout 15 --max-time 300 \
        "https://nodejs.org/dist/$version/$dist.tar.xz" -o "$download/$dist.tar.xz"
    verify_archive "$download/$dist.tar.xz"
    mv -n "$download/$dist.tar.xz" "$archive"
fi
verify_archive "$archive"

# Extract on the bin destination's filesystem so link(2) can publish complete
# regular files atomically without ever replacing an existing file or symlink.
tools=$(mktemp -d "$bin/.setup-pi.XXXXXX")
tar -xJf "$archive" -C "$tools" "$dist/bin/node" "$dist/LICENSE" "$dist/lib/node_modules/npm"
runtime=$tools/$dist
install_file() {
    local source=$1 target=$2
    if [[ -e $target || -L $target ]]; then
        [[ -f $target && ! -L $target ]] && cmp -s "$source" "$target" || die "refusing to replace $target"
    else
        link "$source" "$target"
    fi
}

# Only the verified toolchain and public locked packages participate. Do not
# pass ambient API keys, npm credentials/config or NODE_OPTIONS into Node.
run_node() { env -i HOME="$stage" PATH="$runtime/bin:$PATH" "$runtime/bin/node" "$@"; }
npm_cache=${npm_config_cache:-$HOME/.npm}
[[ $npm_cache == /* ]] || npm_cache=$PWD/$npm_cache
cp "$helper"/{package.json,package-lock.json,pi-helper.mjs} "$stage/"
cd "$stage"
run_node "$runtime/lib/node_modules/npm/bin/npm-cli.js" ci --prefix "$stage" --cache "$npm_cache" \
    --omit=dev --include=optional --ignore-scripts --engine-strict --prefer-offline --no-audit --no-fund --no-update-notifier
# npm treats a missing optional native package as success. Require the local binding
# explicitly so an existing node_modules higher in the tree cannot mask a failed install.
run_node -e "require('./node_modules/@napi-rs/keyring-darwin-arm64')"
run_node pi-helper.mjs list-providers >/dev/null

install_file "$runtime/LICENSE" "$bin/node.LICENSE"
install_file "$runtime/bin/node" "$bin/node"
[[ -x $bin/node ]] || die "not executable: $bin/node"

# macOS can swap two nonempty directories in one rename. On failure the old
# tree stays live; on success it lands in the private stage for cleanup.
mode=new
if [[ -d $modules ]]; then mode=replace; fi
osascript -l JavaScript - "$stage/node_modules" "$modules" "$mode" <<'JS'
function run([source, target, mode]) {
    ObjC.bindFunction('renamex_np', ['int', ['char *', 'char *', 'unsigned int']]);
    const flag = mode === 'replace' ? 0x2 : 0x4; // RENAME_SWAP or RENAME_EXCL
    if ($.renamex_np(source, target, flag) !== 0) throw Error('Could not publish ' + target);
}
JS
printf 'Pi helper ready: %s + %s\n' "$bin/node" "$modules"
