# Third-party software in VidDown

VidDown itself is released under the **MIT License** (see `LICENSE`,
Copyright (c) 2024-2026 SAIMUM HABIB). VidDown packages and uses the open-source
programs and libraries below. Each keeps its own license, which applies to that
component only. This file is informational and is not legal advice.

## Bundled in the packages (.deb / Windows installer / portable zip)

| Component | Used for | License | Project / source |
|---|---|---|---|
| **yt-dlp** | reads video sites and downloads (imported as a Python library) | Unlicense (public domain) | https://github.com/yt-dlp/yt-dlp |
| **yt-dlp-ejs** | JavaScript solver for YouTube's challenges | Unlicense AND MIT AND ISC | https://github.com/yt-dlp/ejs |
| **FFmpeg** (`ffmpeg`, `ffprobe`) | merging, container remuxing, trimming, audio conversion, subtitle / cover / metadata embedding | GPL (*GPL* builds: static on Linux; on Windows the *shared* build, i.e. `ffmpeg.exe`, `ffprobe.exe` and the `avcodec`/`avformat`/`avfilter`/`avutil`/`avdevice`/`swscale`/`swresample` DLLs next to them in `bin\`) | https://ffmpeg.org |
| **aria2** (`aria2c`) | multi-connection downloads | GPL-2.0-or-later | https://github.com/aria2/aria2 |
| **Deno** (`deno`) — *"with-js" builds only* | JavaScript runtime that runs the YouTube solver | MIT (Copyright the Deno authors) | https://github.com/denoland/deno |
| **Python** and **Tcl/Tk** (tkinter) | the runtime and the user interface toolkit | PSF-2.0; Tcl/Tk license (BSD-style) | https://www.python.org, https://www.tcl-lang.org |
| **Pillow** | images / icons | MIT-CMU (HPND) | https://github.com/python-pillow/Pillow |
| **requests**, **urllib3** | HTTP for yt-dlp | Apache-2.0; MIT | https://github.com/psf/requests, https://github.com/urllib3/urllib3 |
| **certifi** | CA certificate bundle (unmodified) | MPL-2.0 | https://github.com/certifi/python-certifi |
| **websockets** | live-stream support in yt-dlp | BSD-3-Clause | https://github.com/python-websockets/websockets |
| **pycryptodomex** | decryption in yt-dlp (e.g. encrypted HLS) | BSD-2-Clause and public domain | https://github.com/Legrandin/pycryptodome |
| **Brotli** | decompression in yt-dlp | MIT | https://github.com/google/brotli |
| **SecretStorage** (Linux .deb only) | reads the keyring key for Chromium-browser cookies | BSD-3-Clause | https://github.com/mitya57/secretstorage |
| **jeepney** (Linux .deb only) | D-Bus client used by SecretStorage | MIT | https://gitlab.com/takluyver/jeepney |
| **cryptography** (Linux .deb only) | used by SecretStorage | Apache-2.0 or BSD-3-Clause | https://github.com/pyca/cryptography |
| **NewPipe Extractor** (`viddown-newpipe[.exe]` native helper, or `newpipe-helper.jar`) — *only in builds that include the NewPipe engine* | finds the streams of a link when Settings → Download engine is NewPipe; runs as a separate program. The native helper is NewPipe Extractor compiled with GraalVM by the viddown-native-bundle repo; it is imported from https://github.com/saimumhabib/viddown-native-bundle (release `newpipe-v<version>`), whose zip lists the exact version, the Java libraries inside it and their licence files; the import keeps them in `bin/newpipe/native-helper-info/`, which is bundled with the helper | GPL-3.0-or-later | https://github.com/TeamNewPipe/NewPipeExtractor |
| **certifi CA bundle** (`bin/newpipe/cacert.pem`) — *only with the native NewPipe helper* | the trusted certificates the native helper uses for HTTPS | MPL-2.0 | https://github.com/certifi/python-certifi |
| **Java runtime** (OpenJDK, `bin/jre`) — *only if bundled with the NewPipe engine* | runs the NewPipe helper | GPL-2.0 with the Classpath Exception | https://openjdk.org |
| **PyInstaller** (bootloader) | turns the app into a standalone program | GPL-2.0-or-later *with a special exception* that allows bundled programs under any license | https://github.com/pyinstaller/pyinstaller |

The Python libraries are the ones installed with `yt-dlp[default]` (see
`requirements.txt`); the exact versions are whatever the build installed.

**Deliberately not bundled:** `mutagen` (GPL-2.0-or-later) — the build excludes it
(`--exclude-module mutagen`). yt-dlp would need it to put a cover picture into
`.opus` / `.ogg` / `.flac` files, so VidDown does that step itself with FFmpeg
(`core/cover_embed.py`); no other format needs it.

## About the GPL programs (FFmpeg, aria2)

FFmpeg and aria2 are separate programs that VidDown starts as child processes;
they are not linked into VidDown, and VidDown's MIT license does not change
theirs. They are shipped unmodified, as the builds named below (static on Linux, shared libraries on Windows):

* **Linux (.deb):** FFmpeg from https://johnvansickle.com/ffmpeg/ (fallback:
  https://github.com/BtbN/FFmpeg-Builds); aria2 from Debian's `aria2` package.
* **Windows:** FFmpeg from https://github.com/BtbN/FFmpeg-Builds (`win64-gpl-shared`, pinned to the 8.1 release branch); aria2 from
  https://github.com/aria2/aria2/releases (official Windows build).

You can read each program's exact license by running `ffmpeg -L` or
`aria2c --version`, and the corresponding source code is available from the
upstream projects above (Debian: `apt source aria2`). To ask the VidDown
maintainer for the source of the exact build you have, open an issue at
https://github.com/saimum10/VidDown/issues.

## Files and services

VidDown downloads content from third-party websites at your request. It does not
grant any rights to that content; you are responsible for respecting the
copyright and the terms of the sites you download from.
