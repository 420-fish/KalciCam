#!/usr/bin/env bash
# Builds KalciCam-x86_64.AppImage. Run on any x86_64 Linux from the project root:
#     bash packaging/linux/build_appimage.sh
# Needs: python3 (3.10+) with venv, curl. Build on an older distro (e.g. Ubuntu 22.04)
# so the AppImage also runs on older systems.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
OUT="$ROOT/dist"
APPDIR="$ROOT/build/AppDir"

echo "==> Python environment"
"$PY" -m venv .venv-build
. .venv-build/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt pyinstaller

echo "==> PyInstaller"
pyinstaller packaging/kalcicam.spec --noconfirm --distpath "$OUT" --workpath build/pyi

echo "==> AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/lib/kalcicam"
cp -a "$OUT/KalciCam/." "$APPDIR/usr/lib/kalcicam/"

# Qt's X11 plugin needs libxcb-cursor, which many distros don't install by default.
for lib in libxcb-cursor.so.0; do
    src="$(ldconfig -p | awk -v l="$lib" '$1 == l { print $NF; exit }')"
    if [ -n "$src" ]; then
        cp -L "$src" "$APPDIR/usr/lib/kalcicam/_internal/"
    else
        echo "warning: $lib not found on this build machine (install libxcb-cursor0)" >&2
    fi
done

cp packaging/linux/AppRun "$APPDIR/AppRun"
chmod +x "$APPDIR/AppRun"
cp packaging/linux/kalcicam.desktop "$APPDIR/kalcicam.desktop"
cp kalcicam/assets/icon.png "$APPDIR/kalcicam.png"
cp kalcicam/assets/icon.png "$APPDIR/.DirIcon"

echo "==> appimagetool"
TOOL="$ROOT/build/appimagetool-x86_64.AppImage"
if [ ! -x "$TOOL" ]; then
    curl -fsSL -o "$TOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
    chmod +x "$TOOL"
fi
# --appimage-extract-and-run works even where FUSE isn't available (containers, CI).
ARCH=x86_64 "$TOOL" --appimage-extract-and-run "$APPDIR" "$OUT/KalciCam-x86_64.AppImage"

echo
echo "Done: $OUT/KalciCam-x86_64.AppImage"
