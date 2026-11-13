<p align="center">
  <img src="resources/icon.png" alt="VidDown logo" width="128">
</p>

<h1 align="center">VidDown</h1>

<p align="center">
  A simple video downloader for Linux and Windows, powered by yt-dlp.<br><br>
  <a href="https://github.com/saimum10/VidDown/releases/latest"><img src="https://img.shields.io/github/v/release/saimum10/VidDown?label=version" alt="Latest version"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License"></a>
</p>

## Features

- Paste a link, see a preview (thumbnail, title, duration) and pick a quality from 144p to 4K, or audio only
- Video as MP4, MKV, WebM, MOV or AVI. Audio as MP3, M4A, Opus, FLAC, WAV, or the site's original audio untouched
- Embed thumbnail and metadata, choose subtitles, trim a time range, download whole playlists
- Download queue with progress, speed, ETA, pause / resume / cancel, and a history page
- Fast multi-connection downloads (aria2c), speed limit, proxy, and browser cookies for sites that need a login
- Browser extension: a download button on videos in Chrome, Brave and Edge
- yt-dlp updates itself, and the app can update itself from GitHub Releases

## Install

Download the latest file from [Releases](https://github.com/saimum10/VidDown/releases/latest):

- **Linux (Debian / Ubuntu):** `sudo apt install ./viddown_*.deb`
- **Windows:** the Setup `.exe`, or the portable `.zip`

Each comes in two builds: **with-js** (includes the JavaScript runtime YouTube needs for every video format) and **no-js** (smaller).

To run from source:

```bash
sudo apt install python3-tk ffmpeg aria2
pip install -r requirements.txt --break-system-packages
python3 main.py
```

## Browser extension

The extension works in Chrome, Brave and Edge. It is not on the Web Store, so it is added by hand:

1. In VidDown open **Settings → Browser extension → Open folder** (or use the `browser_extension` folder of this repository).
2. In the browser open `chrome://extensions` (`brave://extensions`, `edge://extensions`) and turn on **Developer mode**.
3. Click **Load unpacked** and select that folder.
4. Pin the VidDown icon. Keep the VidDown app running, hover over a video and click **VidDown**.

More details: [browser_extension/README.md](browser_extension/README.md).

## Build

```bash
./build_deb.sh                                                  # Linux .deb
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1    # Windows installer + portable zip
python3 -m unittest discover -s tests                           # tests
```

GitHub Actions builds a new release automatically whenever yt-dlp publishes a new version.

## License

[MIT](LICENSE). Third-party tools (yt-dlp, FFmpeg, aria2, Deno and others) keep their own licenses, listed in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Only download content you have the right to download, and follow the terms of the sites you use.

## Author

**SAIMUM HABIB** — [github.com/saimum10](https://github.com/saimum10)
