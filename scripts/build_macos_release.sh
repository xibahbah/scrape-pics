#!/bin/zsh
# Build a standalone, signable Jade.app for distribution outside this repository.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
APP_NAME="Jade"
BUNDLE_ID="${JADE_BUNDLE_ID:-com.jadestudio.app}"
VERSION="${JADE_VERSION:-0.4.0}"
ICON="$ROOT/Jade.app/Contents/Resources/Jade-v3.icns"
DIST_DIR="$ROOT/dist"
APP_PATH="$DIST_DIR/$APP_NAME.app"

if [[ ! -x "$PYTHON" ]]; then
  print -u2 "Jade's Python environment was not found at: $PYTHON"
  exit 1
fi

if [[ ! -f "$ICON" ]]; then
  print -u2 "Jade icon not found: $ICON"
  exit 1
fi

"$PYTHON" -m pip install --upgrade "pyinstaller>=6.11"
rm -rf "$ROOT/build" "$DIST_DIR" "$ROOT/$APP_NAME.spec"

"$PYTHON" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --icon "$ICON" \
  --add-data "$ROOT/web:web" \
  --collect-all webview \
  --hidden-import webview.platforms.cocoa \
  "$ROOT/palette_studio_app.py"

PLIST="$APP_PATH/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.photography" "$PLIST" 2>/dev/null || \
  /usr/libexec/PlistBuddy -c "Set :LSApplicationCategoryType public.app-category.photography" "$PLIST"

if [[ -n "${CODE_SIGN_IDENTITY:-}" ]]; then
  codesign --force --deep --options runtime --timestamp --sign "$CODE_SIGN_IDENTITY" "$APP_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
else
  # Keep locally-built artifacts launchable while waiting for a Developer ID.
  codesign --force --deep --sign - "$APP_PATH"
  codesign --verify --deep --strict --verbose=2 "$APP_PATH"
fi

ARCHIVE="$DIST_DIR/$APP_NAME.zip"
rm -f "$ARCHIVE"
ditto -c -k --keepParent "$APP_PATH" "$ARCHIVE"

if [[ -n "${NOTARYTOOL_PROFILE:-}" ]]; then
  if [[ -z "${CODE_SIGN_IDENTITY:-}" ]]; then
    print -u2 "Set CODE_SIGN_IDENTITY before notarizing Jade."
    exit 1
  fi
  xcrun notarytool submit "$ARCHIVE" --keychain-profile "$NOTARYTOOL_PROFILE" --wait
  xcrun stapler staple "$APP_PATH"
  rm -f "$ARCHIVE"
  ditto -c -k --keepParent "$APP_PATH" "$ARCHIVE"
fi

print "Built: $APP_PATH"
print "Archive: $ARCHIVE"
