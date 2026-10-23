#!/usr/bin/env bash
# =============================================================================
# build_deb.sh — VidDown (SAIMUM HABIB)
# Builds a complete .deb package with a single command.
#
# ব্যবহার:
#   chmod +x build_deb.sh
#   ./build_deb.sh                      # with-js (default): Deno JS runtime bundled
#   ./build_deb.sh --variant no-js      # smaller package, no JS runtime bundled
#
# Two flavours exist because YouTube needs a JavaScript runtime (Deno) plus
# yt-dlp-ejs to offer every format. Output file names carry the flavour, and
# the in-app updater relies on that:
#   viddown_<version>-1_<arch>_with-js.deb   /   viddown_<version>-1_<arch>_no-js.deb
# (DENO_VERSION=v2.x.y pins the Deno release; default: latest.)
#
# This script first checks which dependencies are installed and which are missing.
# যা আগে থেকেই আছে সেটা SKIP করবে, যা নেই সেটা automatically apt/pip দিয়ে
# It installs missing ones, then proceeds to ffmpeg download, PyInstaller bundle, and .deb build.
#
# sudo password চাইতে পারে (system package ইন্সটল করার জন্য) — এটা স্বাভাবিক।
# =============================================================================

set -euo pipefail

APP_NAME="viddown"

# ── build flavour: with-js | no-js ───────────────────────────────────────────
VARIANT="${VIDDOWN_VARIANT:-with-js}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --variant)    VARIANT="${2:-}"; shift 2 || break ;;
        --variant=*)  VARIANT="${1#*=}"; shift ;;
        -h|--help)    sed -n '2,20p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *)            echo "Unknown option: $1 (use --variant with-js|no-js)" >&2; exit 2 ;;
    esac
done
case "$VARIANT" in
    with-js|no-js) ;;
    *) echo "Invalid --variant '$VARIANT' (use with-js or no-js)" >&2; exit 2 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# version.py is the single source of truth for the app version (see its own
# docstring) — read it here instead of keeping a second hardcoded copy that
# only this script would remember to update.
APP_VERSION="$(python3 -c "
import sys; sys.path.insert(0, '$SCRIPT_DIR')
from version import __version__
print(f'{__version__}-1')
")"
ARCH=$(dpkg --print-architecture 2>/dev/null || echo "amd64")
MAINTAINER="SAIMUM HABIB <saimum10@github>"
if [[ "$VARIANT" == "with-js" ]]; then
    DESCRIPTION="Smart, self-contained video downloader. Bundles yt-dlp, ffmpeg, aria2c, Deno."
else
    DESCRIPTION="Smart, self-contained video downloader. Bundles yt-dlp, ffmpeg, aria2c."
fi

BUILD_DIR="$SCRIPT_DIR/build"
DEB_ROOT="$BUILD_DIR/${APP_NAME}_${APP_VERSION}_${ARCH}_${VARIANT}"
DEB_FILE="$SCRIPT_DIR/${APP_NAME}_${APP_VERSION}_${ARCH}_${VARIANT}.deb"

# ── রঙিন লগ ──────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $*"; }
skip()  { echo -e "${CYAN}[•]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*"; exit 1; }
step()  { echo ""; echo -e "${CYAN}━━━ $* ━━━${NC}"; }

# কোনো কমান্ড অপ্রত্যাশিতভাবে ব্যর্থ হলে script যেন কখনো নীরবে থেমে না যায় —
# Shows exactly which line and command caused an issue. (Before this,
# trap না থাকায় apt/pip install ব্যর্থ হলে কোনো বার্তা ছাড়াই script থেমে যেত।)
trap 'echo -e "${RED}[✗] Script stopped unexpectedly — line ${LINENO}: ${BASH_COMMAND}${NC}" >&2' ERR

echo ""
echo "╔══════════════════════════════════════════════╗"
echo "║   VidDown (SAIMUM HABIB) — .deb builder     ║"
echo "╚══════════════════════════════════════════════╝"
echo "   flavour: $VARIANT"

# ── initial check: ensure script is run from the correct folder ──────────────
[[ -f "$SCRIPT_DIR/main.py" ]] || error "main.py not found. Run from inside the VidDown folder."
[[ -f "$SCRIPT_DIR/LICENSE" && -f "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" ]] \
    || error "LICENSE / THIRD_PARTY_NOTICES.md missing — they ship inside the package."

# sudo লাগবে কিনা ঠিক করা (root হিসেবে চললে sudo লাগবে না)
SUDO=""
if [[ "${EUID}" -ne 0 ]]; then
    if command -v sudo &>/dev/null; then
        SUDO="sudo"
    else
        warn "Not root and sudo not found — system package auto-install may not work."
    fi
fi

# =============================================================================
# ── step 1: system (apt) dependency check and install ───────────────────────
# =============================================================================
step "Step 1/7 — Checking system dependencies"

REQUIRED_APT_PKGS=(
    python3
    python3-dev
    python3-tk
    python3-pip
    wget
    tar
    xz-utils
    fakeroot
    dpkg-dev
    aria2
    desktop-file-utils
    unzip
)

missing_apt=()
for pkg in "${REQUIRED_APT_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        skip "$pkg — আগে থেকেই ইন্সটল করা আছে, skip।"
    else
        warn "$pkg — not found, needs to be installed."
        missing_apt+=("$pkg")
    fi
done

if [[ ${#missing_apt[@]} -gt 0 ]]; then
    info "Installing: ${missing_apt[*]}"

    if ! $SUDO apt-get update -qq; then
        warn "apt-get update had an issue (package list may be stale), continuing anyway..."
    fi

    if ! $SUDO apt-get install -y "${missing_apt[@]}"; then
        error "apt-get install failed. Check the apt error message above.
   ম্যানুয়ালি চেষ্টা করুন: sudo apt install ${missing_apt[*]}"
    fi

    # ইন্সটলের পর আবার যাচাই করি
    for pkg in "${missing_apt[@]}"; do
        dpkg -s "$pkg" &>/dev/null || error "$pkg install failed. Try manually: sudo apt install $pkg"
    done
    info "All missing system packages installed successfully."
else
    info "All system dependencies already present — nothing to install."
fi

# =============================================================================
# ── step 2: Python (pip) dependency check and install ───────────────────────
# =============================================================================
step "Step 2/7 — Checking Python (pip) dependencies"

missing_pip=()

# PyInstaller is a CLI tool — check it as a command
if command -v pyinstaller &>/dev/null; then
    skip "pyinstaller — আগে থেকেই ইন্সটল করা আছে, skip।"
else
    warn "pyinstaller — not found, needs to be installed."
    missing_pip+=("pyinstaller")
fi

# Check remaining packages by importing them as Python modules (module_name:pip_package_name)
# yt-dlp[default] = yt-dlp + certifi, requests, websockets, pycryptodomex, ... and
# yt-dlp-ejs (the JavaScript solver for YouTube; yt-dlp pins its exact version).
PY_MODULES=("yt_dlp:yt-dlp[default]" "yt_dlp_ejs:yt-dlp[default]" "PIL:Pillow")
for entry in "${PY_MODULES[@]}"; do
    mod_name="${entry%%:*}"
    pip_name="${entry##*:}"
    if python3 -c "import ${mod_name}" &>/dev/null; then
        skip "$pip_name — আগে থেকেই ইন্সটল করা আছে, skip।"
    else
        warn "$pip_name — not found, needs to be installed."
        missing_pip+=("$pip_name")
    fi
done

if [[ ${#missing_pip[@]} -gt 0 ]]; then
    info "Installing via pip: ${missing_pip[*]}"

    # On minimal systems 'pip3' may be absent but 'python3 -m pip' may still work
    if command -v pip3 &>/dev/null; then
        PIP_CMD=(pip3)
    else
        PIP_CMD=(python3 -m pip)
    fi

    PIP_ERR_LOG="$(mktemp)"
    # নতুন Debian/Ubuntu এ pip এর জন্য --break-system-packages লাগে (PEP 668),
    # পুরনো সিস্টেমে এই flag টা থাকেই না — তাই আগে flag সহ চেষ্টা করি।
    if "${PIP_CMD[@]}" install "${missing_pip[@]}" --break-system-packages --quiet 2>"$PIP_ERR_LOG"; then
        rm -f "$PIP_ERR_LOG"
    elif grep -qi "no such option\|unrecognized arguments" "$PIP_ERR_LOG"; then
        warn "--break-system-packages not supported by this pip version, retrying without flag..."
        rm -f "$PIP_ERR_LOG"
        "${PIP_CMD[@]}" install "${missing_pip[@]}" --quiet \
            || error "pip install failed. Try manually: pip3 install ${missing_pip[*]}"
    else
        cat "$PIP_ERR_LOG" >&2
        rm -f "$PIP_ERR_LOG"
        error "pip install failed. Check the pip error message above."
    fi

    info "All missing pip packages installed successfully."
else
    info "All pip dependencies already present — nothing to install."
fi

# tkinter আলাদাভাবে নিশ্চিত হয়ে নিচ্ছি (python3-tk apt প্যাকেজ থেকে আসে, pip থেকে না)
python3 -c "import tkinter" &>/dev/null || error "tkinter import failed even after installing python3-tk. Check manually."
skip "tkinter — import ঠিকভাবে কাজ করছে।"

# =============================================================================
# ── step 3: ffmpeg static binary ────────────────────────────────────────────
# =============================================================================
step "Step 3/7 — Checking ffmpeg / ffprobe"

FFMPEG_BIN="$SCRIPT_DIR/bin/ffmpeg"
FFPROBE_BIN="$SCRIPT_DIR/bin/ffprobe"

if [[ -f "$FFMPEG_BIN" ]] && [[ -f "$FFPROBE_BIN" ]]; then
    skip "ffmpeg/ffprobe — bin/ ফোল্ডারে আগে থেকেই আছে, skip।"
else
    PRIMARY_FF_URL="https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"
    FALLBACK_FF_URL="https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-linux64-gpl.tar.xz"

    # Integrity check for a downloaded archive against the checksum file its
    # publisher serves next to it ($3 = md5|sha256, $4 = name inside that file).
    # Catches truncated/corrupted downloads and a mismatching mirror; it can't
    # protect against a publisher that is itself compromised — pin a known hash
    # here for that.
    verify_ff_archive() {
        local sumfile expected actual
        sumfile="$(mktemp)"
        if ! wget --timeout=20 --tries=2 -q -O "$sumfile" "$2" || [[ ! -s "$sumfile" ]]; then
            warn "Checksum file could not be downloaded ($2)"
            rm -f "$sumfile"; return 1
        fi
        if [[ "$3" == "sha256" ]]; then
            expected="$(grep -F "$4" "$sumfile" | awk '{print $1}' | head -n1)"
            actual="$(sha256sum "$1" | awk '{print $1}')"
        else
            expected="$(awk '{print $1}' "$sumfile" | head -n1)"
            actual="$(md5sum "$1" | awk '{print $1}')"
        fi
        rm -f "$sumfile"
        if [[ -z "$expected" || "$expected" != "$actual" ]]; then
            warn "Checksum mismatch for downloaded ffmpeg archive — discarding it."
            return 1
        fi
    }

    TMP_FF="$(mktemp -d)"
    ARCHIVE="$TMP_FF/ffmpeg.tar.xz"
    DOWNLOAD_OK=false

    info "Downloading ffmpeg (source 1/2: johnvansickle.com)..."
    if wget --timeout=20 --tries=2 -q --show-progress -O "$ARCHIVE" "$PRIMARY_FF_URL" && [[ -s "$ARCHIVE" ]] \
            && verify_ff_archive "$ARCHIVE" "$PRIMARY_FF_URL.md5" md5 "ffmpeg-release-amd64-static.tar.xz"; then
        DOWNLOAD_OK=true
    else
        warn "Download from johnvansickle.com failed (possible network/firewall issue)."
        rm -f "$ARCHIVE"

        info "Trying alternative source (source 2/2: GitHub - BtbN/FFmpeg-Builds)..."
        if wget --timeout=20 --tries=2 -q --show-progress -O "$ARCHIVE" "$FALLBACK_FF_URL" && [[ -s "$ARCHIVE" ]] \
            && verify_ff_archive "$ARCHIVE" "${FALLBACK_FF_URL%/*}/checksums.sha256" sha256 "ffmpeg-master-latest-linux64-gpl.tar.xz"; then
            DOWNLOAD_OK=true
        else
            rm -f "$ARCHIVE"
        fi
    fi

    if [[ "$DOWNLOAD_OK" != true ]]; then
        rm -rf "$TMP_FF"
        error "ffmpeg download failed — both sources failed.
   সম্ভাব্য কারণ: ইন্টারনেট সংযোগ অস্থির, অথবা firewall/proxy/ISP
   johnvansickle.com এবং github.com উভয়ই ব্লক করছে।
   ম্যানুয়াল সমাধান: যেকোনো ব্রাউজার দিয়ে একটা ffmpeg static build (linux amd64)
   ডাউনলোড করে ffmpeg ও ffprobe বাইনারি দুটো এই পাথে রাখুন, তারপর script আবার চালান:
     $SCRIPT_DIR/bin/ffmpeg
     $SCRIPT_DIR/bin/ffprobe"
    fi

    info "Download complete, extracting..."
    # দুটো সোর্সের ভেতরের ফোল্ডার গঠন আলাদা (johnvansickle: ffmpeg/ffprobe সরাসরি
    # top-level এ; BtbN: bin/ffmpeg, bin/ffprobe একটা সাবফোল্ডারে) — তাই
    # --strip-components নির্ভর না করে find দিয়ে যেখানেই থাকুক খুঁজে বের করছি।
    tar -xf "$ARCHIVE" -C "$TMP_FF" \
        || error "ffmpeg archive extraction failed — downloaded file may be corrupt."

    FOUND_FFMPEG="$(find "$TMP_FF" -type f -name "ffmpeg" -perm -u+x | head -1)"
    FOUND_FFPROBE="$(find "$TMP_FF" -type f -name "ffprobe" -perm -u+x | head -1)"

    if [[ -z "$FOUND_FFMPEG" ]] || [[ -z "$FOUND_FFPROBE" ]]; then
        rm -rf "$TMP_FF"
        error "ffmpeg/ffprobe binary not found inside the downloaded archive."
    fi

    mkdir -p "$SCRIPT_DIR/bin"
    cp "$FOUND_FFMPEG"  "$FFMPEG_BIN"
    cp "$FOUND_FFPROBE" "$FFPROBE_BIN"
    chmod +x "$FFMPEG_BIN" "$FFPROBE_BIN"
    rm -rf "$TMP_FF"
    info "ffmpeg ready: $(${FFMPEG_BIN} -version 2>&1 | head -1)"
fi

# =============================================================================
# ── step 4: aria2c binary ───────────────────────────────────────────────────
# =============================================================================
step "Step 4/7 — Checking aria2c"

ARIA2C_BIN="$SCRIPT_DIR/bin/aria2c"

if [[ -f "$ARIA2C_BIN" ]]; then
    skip "aria2c — bin/ ফোল্ডারে আগে থেকেই আছে, skip।"
elif command -v aria2c &>/dev/null; then
    info "Copying system aria2c (installed via apt in step 1) to bin/..."
    mkdir -p "$SCRIPT_DIR/bin"
    cp "$(command -v aria2c)" "$ARIA2C_BIN"
    chmod +x "$ARIA2C_BIN"
    info "aria2c ready for bundling."
else
    # This branch should not be reached since aria2 was installed via apt in step 1,
    # কিন্তু কোনো কারণে না থাকলে build থেমে না গিয়ে placeholder দিয়ে চালিয়ে যায়
    warn "aria2c not found — multi-connection fast download will not work."
    mkdir -p "$SCRIPT_DIR/bin"
    # No fake placeholder binary: a stub that exits 1 would be picked up as
    # the real aria2c and make every download fail. Without a file the app
    # simply uses yt-dlp's own downloader.
    rm -f "$ARIA2C_BIN"
fi

# =============================================================================
# ── step 4b: Deno (JavaScript runtime) — with-js flavour only ───────────────
# =============================================================================
step "Step 4b/7 — JavaScript runtime (Deno) [$VARIANT]"

DENO_BIN="$SCRIPT_DIR/bin/deno"
if [[ "$VARIANT" != "with-js" ]]; then
    skip "no-js flavour — Deno is not bundled."
elif [[ -x "$DENO_BIN" ]] && "$DENO_BIN" --version >/dev/null 2>&1; then
    skip "deno — bin/ ফোল্ডারে আগে থেকেই আছে ($("$DENO_BIN" --version | head -1)), skip।"
else
    case "$ARCH" in
        amd64) DENO_TARGET="x86_64-unknown-linux-gnu" ;;
        arm64) DENO_TARGET="aarch64-unknown-linux-gnu" ;;
        *)     error "Deno has no build for architecture '$ARCH' — use: ./build_deb.sh --variant no-js" ;;
    esac
    DENO_VERSION="${DENO_VERSION:-latest}"
    if [[ "$DENO_VERSION" == "latest" ]]; then
        DENO_BASE="https://github.com/denoland/deno/releases/latest/download"
    else
        DENO_BASE="https://github.com/denoland/deno/releases/download/${DENO_VERSION}"
    fi
    DENO_ZIP_NAME="deno-${DENO_TARGET}.zip"
    TMP_DENO="$(mktemp -d)"

    info "Downloading $DENO_ZIP_NAME ($DENO_VERSION)..."
    wget --timeout=30 --tries=3 -q --show-progress -O "$TMP_DENO/$DENO_ZIP_NAME" "$DENO_BASE/$DENO_ZIP_NAME" \
        && [[ -s "$TMP_DENO/$DENO_ZIP_NAME" ]] \
        || { rm -rf "$TMP_DENO"; error "Deno download failed. Retry, or build without it: ./build_deb.sh --variant no-js"; }
    # Integrity check against the .sha256sum file Deno publishes next to the archive.
    wget --timeout=30 --tries=3 -q -O "$TMP_DENO/sum" "$DENO_BASE/$DENO_ZIP_NAME.sha256sum" \
        && [[ -s "$TMP_DENO/sum" ]] \
        || { rm -rf "$TMP_DENO"; error "Deno checksum file could not be downloaded — refusing an unverified binary."; }
    # first 64-hex-digit token, whatever layout the .sha256sum file has
    DENO_EXPECTED="$(grep -Eio '\b[0-9a-f]{64}\b' "$TMP_DENO/sum" | head -n1 | tr 'A-F' 'a-f')"
    DENO_ACTUAL="$(sha256sum "$TMP_DENO/$DENO_ZIP_NAME" | awk '{print $1}')"
    if [[ -z "$DENO_EXPECTED" || "$DENO_EXPECTED" != "$DENO_ACTUAL" ]]; then
        rm -rf "$TMP_DENO"
        error "Deno archive checksum mismatch (expected '$DENO_EXPECTED', got '$DENO_ACTUAL')."
    fi
    info "Deno archive checksum verified."

    unzip -q -o "$TMP_DENO/$DENO_ZIP_NAME" -d "$TMP_DENO/x" \
        || { rm -rf "$TMP_DENO"; error "Deno archive extraction failed."; }
    [[ -f "$TMP_DENO/x/deno" ]] || { rm -rf "$TMP_DENO"; error "deno binary not found inside the archive."; }
    mkdir -p "$SCRIPT_DIR/bin"
    cp "$TMP_DENO/x/deno" "$DENO_BIN"
    chmod +x "$DENO_BIN"
    rm -rf "$TMP_DENO"
    "$DENO_BIN" --version >/dev/null 2>&1 || error "Downloaded deno does not run on this system."
    info "deno ready: $("$DENO_BIN" --version | head -1)"
fi

# =============================================================================
# ── step 5: PyInstaller bundle ──────────────────────────────────────────────
# =============================================================================
step "Step 5/7 — Building PyInstaller bundle (this may take a while)"

cd "$SCRIPT_DIR"
rm -rf "$BUILD_DIR/pyinstaller_out" "$BUILD_DIR/pyinstaller_work" dist build __pycache__

# Tells the running app which flavour it is (core/buildinfo.py); the in-app
# updater uses it to fetch the same flavour again.
mkdir -p "$BUILD_DIR"
printf '%s\n' "$VARIANT" > "$BUILD_DIR/build_variant.txt"

# Deno goes in as plain data (copied byte-for-byte, not binary-analysed)
DENO_ARGS=()
if [[ "$VARIANT" == "with-js" ]]; then
    DENO_ARGS=("--add-data" "$DENO_BIN:bin")
fi

# aria2c — শুধু ELF binary হলে bundle করব (placeholder shell script নয়)
ARIA2C_ARGS=()
if file "$SCRIPT_DIR/bin/aria2c" 2>/dev/null | grep -q "ELF"; then
    ARIA2C_ARGS=("--add-binary" "$SCRIPT_DIR/bin/aria2c:bin")
fi

pyinstaller \
    --noconfirm \
    --onedir \
    --name "$APP_NAME" \
    --distpath "$BUILD_DIR/pyinstaller_out" \
    --workpath "$BUILD_DIR/pyinstaller_work" \
    --specpath "$BUILD_DIR" \
    --add-data "$SCRIPT_DIR/resources:resources" \
    --add-data "$SCRIPT_DIR/browser_extension:browser_extension" \
    --add-binary "$SCRIPT_DIR/bin/ffmpeg:bin" \
    --add-binary "$SCRIPT_DIR/bin/ffprobe:bin" \
    --add-data "$BUILD_DIR/build_variant.txt:resources" \
    --add-data "$SCRIPT_DIR/LICENSE:licenses" \
    --add-data "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md:licenses" \
    --exclude-module mutagen \
    "${ARIA2C_ARGS[@]}" \
    "${DENO_ARGS[@]}" \
    --collect-all yt_dlp \
    --collect-all yt_dlp_ejs \
    --hidden-import PIL \
    --hidden-import PIL.Image \
    --hidden-import PIL.ImageTk \
    --hidden-import version \
    --hidden-import tkinter \
    --hidden-import tkinter.ttk \
    --hidden-import tkinter.font \
    --hidden-import tkinter.filedialog \
    --hidden-import tkinter.messagebox \
    main.py

info "PyInstaller bundle created."

PYINST_DIR="$BUILD_DIR/pyinstaller_out/$APP_NAME"
[[ -d "$PYINST_DIR" ]] || error "PyInstaller output ফোল্ডার পাওয়া যায়নি: $PYINST_DIR"
# Fail the build rather than ship a package that silently lacks the tool
# required by trim/merge/post-processing. Support PyInstaller 5 and 6 layouts.
BUNDLED_FFMPEG="$(find "$PYINST_DIR" -type f -name ffmpeg -perm -u+x -print -quit 2>/dev/null || true)"
BUNDLED_FFPROBE="$(find "$PYINST_DIR" -type f -name ffprobe -perm -u+x -print -quit 2>/dev/null || true)"
[[ -n "$BUNDLED_FFMPEG" ]] || error "Build output-এ bundled ffmpeg পাওয়া যায়নি — এই প্যাকেজ trim করতে পারবে না।"
[[ -n "$BUNDLED_FFPROBE" ]] || error "Build output-এ bundled ffprobe পাওয়া যায়নি।"
"$BUNDLED_FFMPEG" -version >/dev/null 2>&1 || error "Bundled ffmpeg execute হচ্ছে না; binary/architecture পরীক্ষা করুন।"
info "Bundled ffmpeg verified: $BUNDLED_FFMPEG"

# The flavour marker must be inside the package, and the YouTube solver files with it.
BUNDLED_MARKER="$(find "$PYINST_DIR" -type f -name build_variant.txt -print -quit 2>/dev/null || true)"
[[ -n "$BUNDLED_MARKER" && "$(tr -d '[:space:]' < "$BUNDLED_MARKER")" == "$VARIANT" ]] \
    || error "build_variant.txt is missing from the bundle or has the wrong value."
[[ -n "$(find "$PYINST_DIR" -type f -path '*yt_dlp_ejs*' -name 'core.min.js' -print -quit 2>/dev/null)" ]] \
    || error "yt-dlp-ejs solver files are missing from the bundle (is yt-dlp[default] installed?)."
BUNDLED_DENO="$(find "$PYINST_DIR" -type f -name deno -print -quit 2>/dev/null || true)"
if [[ "$VARIANT" == "with-js" ]]; then
    [[ -n "$BUNDLED_DENO" ]] || error "Build output-এ bundled deno পাওয়া যায়নি (with-js flavour)।"
    chmod +x "$BUNDLED_DENO"
    "$BUNDLED_DENO" --version >/dev/null 2>&1 || error "Bundled deno execute হচ্ছে না।"
    info "Bundled Deno verified: $("$BUNDLED_DENO" --version | head -1)"
else
    [[ -z "$BUNDLED_DENO" ]] || error "no-js flavour but a deno binary ended up in the bundle."
    info "no-js flavour verified: no JS runtime in the bundle."
fi

# =============================================================================
# ── step 6: .deb directory structure and file copy ──────────────────────────
# =============================================================================
step "Step 6/7 — Creating .deb package structure"

rm -rf "$DEB_ROOT"
mkdir -p \
    "$DEB_ROOT/DEBIAN" \
    "$DEB_ROOT/usr/lib/$APP_NAME" \
    "$DEB_ROOT/usr/bin" \
    "$DEB_ROOT/usr/share/applications" \
    "$DEB_ROOT/usr/share/pixmaps" \
    "$DEB_ROOT/usr/share/doc/$APP_NAME"

for SIZE in 16 24 32 48 64 96 128 256 512; do
    mkdir -p "$DEB_ROOT/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps"
done

info "ফাইল কপি করছি..."
cp -r "$PYINST_DIR/"* "$DEB_ROOT/usr/lib/$APP_NAME/"

cat > "$DEB_ROOT/usr/bin/$APP_NAME" << 'LAUNCHER'
#!/bin/bash
exec /usr/lib/viddown/viddown "$@"
LAUNCHER
chmod 755 "$DEB_ROOT/usr/bin/$APP_NAME"

cp "$SCRIPT_DIR/viddown.desktop" "$DEB_ROOT/usr/share/applications/"

for SIZE in 16 24 32 48 64 96 128 256 512; do
    SRC="$SCRIPT_DIR/icons/hicolor/${SIZE}x${SIZE}/apps/viddown.png"
    DST="$DEB_ROOT/usr/share/icons/hicolor/${SIZE}x${SIZE}/apps/viddown.png"
    [[ -f "$SRC" ]] && cp "$SRC" "$DST"
done
cp "$SCRIPT_DIR/icons/pixmaps/viddown.png" "$DEB_ROOT/usr/share/pixmaps/"

INSTALLED_SIZE=$(du -sk "$DEB_ROOT/usr" | cut -f1)

cat > "$DEB_ROOT/DEBIAN/control" << EOF
Package: $APP_NAME
Version: $APP_VERSION
Architecture: $ARCH
Maintainer: $MAINTAINER
Installed-Size: $INSTALLED_SIZE
Depends: libc6 (>= 2.17)
Section: net
Priority: optional
Homepage: https://github.com/saimum10/VidDown
Description: $DESCRIPTION
 VidDown is a self-contained video downloader for Debian-based Linux.
 It bundles yt-dlp, ffmpeg, ffprobe, and aria2c — no extra packages
 needed after installation. The "with-js" build also bundles Deno, the
 JavaScript runtime YouTube needs to offer every format. Supports YouTube, Facebook, Instagram,
 TikTok, and 1000+ other sites.
 .
 Features: quality selection (144p to 4K), subtitle embedding,
 playlist support, video trimming, browser cookie import, and
 a download queue with pause/resume/cancel.
EOF

cat > "$DEB_ROOT/DEBIAN/postinst" << 'POSTINST'
#!/bin/bash
set -e
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor || true
fi
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database /usr/share/applications || true
fi
POSTINST
chmod 755 "$DEB_ROOT/DEBIAN/postinst"

cat > "$DEB_ROOT/DEBIAN/prerm" << 'PRERM'
#!/bin/bash
set -e
# uninstall এর সময় runtime এ তৈরি হওয়া extra ফাইল পরিষ্কার করো
# (dpkg tracked ফাইলগুলো dpkg নিজেই মুছবে)
rm -rf /usr/lib/viddown/__pycache__ 2>/dev/null || true
PRERM
chmod 755 "$DEB_ROOT/DEBIAN/prerm"

# ── copyright and changelog (lintian compliance) ────────────────────────────
cat > "$DEB_ROOT/usr/share/doc/$APP_NAME/copyright" << 'COPYRIGHT'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: VidDown
Upstream-Contact: SAIMUM HABIB <saimum10@github>
Source: https://github.com/saimum10/VidDown

Comment: VidDown bundles third-party programs and libraries (yt-dlp, FFmpeg,
 aria2, Deno in the "with-js" build, and others) that keep their own licenses.
 See /usr/share/doc/viddown/THIRD_PARTY_NOTICES.md for the list, licenses and
 where to get their source code.

Files: *
Copyright: 2024-2026, SAIMUM HABIB
License: MIT
 Permission is hereby granted, free of charge, to any person obtaining a copy
 of this software and associated documentation files (the "Software"), to deal
 in the Software without restriction, including without limitation the rights
 to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 copies of the Software, and to permit persons to whom the Software is
 furnished to do so, subject to the following conditions:
 .
 The above copyright notice and this permission notice shall be included in
 all copies or substantial portions of the Software.
 .
 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
COPYRIGHT

# Create changelog and gzip it (Debian policy requirement)
CHANGELOG_TMP="$(mktemp)"
cat > "$CHANGELOG_TMP" << CHANGELOG
viddown (${APP_VERSION}) stable; urgency=low

  * Release ${APP_VERSION}.

 -- SAIMUM HABIB <saimum10@github>  $(date -R)
CHANGELOG
gzip -9 -c "$CHANGELOG_TMP" > "$DEB_ROOT/usr/share/doc/$APP_NAME/changelog.Debian.gz"
cp "$SCRIPT_DIR/LICENSE" "$SCRIPT_DIR/THIRD_PARTY_NOTICES.md" "$DEB_ROOT/usr/share/doc/$APP_NAME/"
rm -f "$CHANGELOG_TMP"
info "copyright and changelog created."

info "Setting file permissions..."
find "$DEB_ROOT" -type f -exec chmod 644 {} \;
find "$DEB_ROOT" -type d -exec chmod 755 {} \;
chmod 755 "$DEB_ROOT/usr/bin/$APP_NAME"
chmod 755 "$DEB_ROOT/usr/lib/$APP_NAME/$APP_NAME"
# PyInstaller 5.x → viddown/bin/ffmpeg
# PyInstaller 6.x → viddown/_internal/bin/ffmpeg
# তাই recursive find — যেখানেই থাকুক +x দাও
find "$DEB_ROOT/usr/lib/$APP_NAME/" -type f \( -name "ffmpeg" -o -name "ffprobe" -o -name "aria2c" -o -name "deno" \) -exec chmod 755 {} \; 2>/dev/null || true
chmod 755 "$DEB_ROOT/DEBIAN/postinst"
chmod 755 "$DEB_ROOT/DEBIAN/prerm"

# =============================================================================
# ── step 7: build the .deb file ─────────────────────────────────────────────
# =============================================================================
step "Step 7/7 — Building .deb file"

if command -v fakeroot &>/dev/null; then
    fakeroot dpkg-deb --build "$DEB_ROOT" "$DEB_FILE"
else
    # fakeroot should have been installed via apt in step 1; this is a fallback
    warn "fakeroot পাওয়া যায়নি, dpkg-deb এর --root-owner-group দিয়ে build করছি।"
    dpkg-deb --build --root-owner-group "$DEB_ROOT" "$DEB_FILE"
fi

# ── সফল! ─────────────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║   ✅  .deb তৈরি হয়েছে!                                      ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
info "ফাইল: $DEB_FILE"
info "সাইজ: $(du -sh "$DEB_FILE" | cut -f1)   (flavour: $VARIANT)"
echo ""
echo "  ইন্সটল করতে:"
echo "    sudo dpkg -i $DEB_FILE"
echo "    sudo apt install -f   ← dependency ঠিক করতে (যদি লাগে)"
echo ""
echo "  আনইন্সটল করতে:"
echo "    sudo apt remove $APP_NAME"
echo ""
