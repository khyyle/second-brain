#!/usr/bin/env bash
# Build a release binary via SwiftPM and wrap it into a .app bundle
# suitable for /Applications + Login Items + Spotlight.
set -euo pipefail

cd "$(dirname "$0")"

APP_NAME="SecondBrainBar"
APP_DISPLAY="Second Brain"
APP_BUNDLE="${APP_DISPLAY}.app"
BUILD_DIR=".build/release"

echo "[build] swift build -c release"
swift build -c release

echo "[bundle] assembling ${APP_BUNDLE}"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

cp "$BUILD_DIR/$APP_NAME" "$APP_BUNDLE/Contents/MacOS/$APP_NAME"
cp "Resources/Info.plist" "$APP_BUNDLE/Contents/Info.plist"

# The menu-bar glyph is loaded at runtime as a template image.
if [ -f "Resources/MenuBarIcon.png" ]; then
    cp "Resources/MenuBarIcon.png" "$APP_BUNDLE/Contents/Resources/MenuBarIcon.png"
fi

# Build AppIcon.icns from the source PNG so the app shows a proper
# logo in Finder, Spotlight, and "Get Info".
if [ -f "Resources/AppIcon.png" ]; then
    echo "[icon] generating AppIcon.icns"
    WORK="$(mktemp -d)"
    ICONSET="$WORK/AppIcon.iconset"
    mkdir -p "$ICONSET"

    # Square the source first. A non-square source fed straight into the
    # per-size resize would be stretched; center-crop to the largest
    # square instead so the icon keeps its aspect ratio.
    W=$(sips -g pixelWidth  "Resources/AppIcon.png" | awk '/pixelWidth/{print $2}')
    H=$(sips -g pixelHeight "Resources/AppIcon.png" | awk '/pixelHeight/{print $2}')
    SQUARE="$WORK/square.png"
    if [ "$W" != "$H" ]; then
        SIDE=$(( W < H ? W : H ))
        echo "[icon] source is ${W}x${H}; center-cropping to ${SIDE}x${SIDE}"
        sips -c "$SIDE" "$SIDE" "Resources/AppIcon.png" --out "$SQUARE" >/dev/null
    else
        cp "Resources/AppIcon.png" "$SQUARE"
    fi

    sips -z 16 16     "$SQUARE" --out "$ICONSET/icon_16x16.png"      >/dev/null
    sips -z 32 32     "$SQUARE" --out "$ICONSET/icon_16x16@2x.png"   >/dev/null
    sips -z 32 32     "$SQUARE" --out "$ICONSET/icon_32x32.png"      >/dev/null
    sips -z 64 64     "$SQUARE" --out "$ICONSET/icon_32x32@2x.png"   >/dev/null
    sips -z 128 128   "$SQUARE" --out "$ICONSET/icon_128x128.png"    >/dev/null
    sips -z 256 256   "$SQUARE" --out "$ICONSET/icon_128x128@2x.png" >/dev/null
    sips -z 256 256   "$SQUARE" --out "$ICONSET/icon_256x256.png"    >/dev/null
    sips -z 512 512   "$SQUARE" --out "$ICONSET/icon_256x256@2x.png" >/dev/null
    sips -z 512 512   "$SQUARE" --out "$ICONSET/icon_512x512.png"    >/dev/null
    sips -z 1024 1024 "$SQUARE" --out "$ICONSET/icon_512x512@2x.png" >/dev/null
    iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/AppIcon.icns"
    rm -rf "$WORK"
fi

# Ad-hoc sign so Gatekeeper lets it launch from /Applications without
# re-quarantining on every build. Can replace `-` with a real Developer ID
# identity for proper notarization
codesign --force --deep --sign - "$APP_BUNDLE" >/dev/null

# Keep the app's pointer to run.sh fresh for dev iteration (install.sh does
# this too). Only when the vault already exists, so a plain build doesn't
# create directories as a side effect.
VAULT="$HOME/second-brain"
if [ -d "$VAULT" ]; then
    REPO_DIR="$(cd ../.. && pwd)"
    printf '%s\n' "$REPO_DIR/run.sh" > "$VAULT/.pipeline-script"
    echo "[link] recorded pipeline path -> $VAULT/.pipeline-script"
fi

echo "built $(pwd)/${APP_BUNDLE}"
echo
echo "Next:"
echo "  open \"${APP_BUNDLE}\"                  # launch once to test"
echo "  cp -r \"${APP_BUNDLE}\" /Applications/  # install (then findable via Spotlight)"
echo "  System Settings -> Login Items           # auto-launch on boot"
