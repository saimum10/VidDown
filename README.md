# VidDown (SAIMUM HABIB)

Standalone, self-contained ভিডিও ডাউনলোডার — Debian-based Linux এর জন্য।
GUI: Tkinter | Engine: yt-dlp | Bundled: ffmpeg, ffprobe, aria2c

## 📁 প্রজেক্ট স্ট্রাকচার

```
VidDown/
├── main.py
├── version.py
├── theme.py
├── requirements.txt
├── build_deb.sh                 # PyInstaller ও .deb build script
├── viddown.desktop
├── bin/                         # Build-এর জন্য binary staging folder + README
├── resources/                   # app icon/resources
├── icons/                       # Linux app icons
├── browser_extension/           # Chrome/Brave/Edge unpacked extension
│   ├── manifest.json
│   ├── background.js
│   ├── content.js
│   ├── popup.html
│   └── overlay.css
├── tests/                       # unit tests: python -m unittest discover -s tests
├── core/
│   ├── __init__.py
│   ├── app_updater.py           # VidDown নিজের আপডেট (GitHub releases)
│   ├── autostart.py
│   ├── bridge_server.py
│   ├── buildinfo.py             # with-js / no-js কোনটা চলছে
│   ├── downloader.py
│   ├── storage.py
│   ├── updater.py               # yt-dlp (+ yt-dlp-ejs) আপডেট
│   └── ytdlp_loader.py
└── ui/
    ├── main_window.py
    ├── nav.py                   # category navigation
    ├── dialogs.py               # Add Download + Settings windows
    ├── icons.py                 # anti-aliased icon set (no emoji)
    ├── widgets.py               # buttons, fields, toggles, progress ...
    ├── log_viewer.py
    ├── scrollable.py
    └── pages/                   # downloads (list), download (add), settings, cookies, about
```

## ▶️ ডেভেলপমেন্টে রান করা

Debian/Ubuntu-এ Tkinter সিস্টেম প্যাকেজ হিসেবে ইন্সটল করতে হয় (pip দিয়ে হয় না):

```bash
sudo apt install python3-tk
pip install -r requirements.txt --break-system-packages
python3 main.py
```

এই মোডে ffmpeg/aria2c এখনো bundled না থাকলে সিস্টেম থেকে (PATH থেকে) খুঁজে নেবে
(`core/downloader.py`-এর `find_binary()` ফাংশন আগে `bin/` ফোল্ডার চেক করে,
তারপর সিস্টেম PATH-এ fallback করে) — তাই টেস্ট করার জন্য
`sudo apt install ffmpeg aria2` করে নিলেই চলবে।

## ✅ যেসব ফিচার ইমপ্লিমেন্ট করা আছে

- URL fetch → প্রিভিউ (থাম্বনেইল, টাইটেল, duration, uploader, views)
- Import Cookies পপআপ → Open in Browser + Add Cookies (auto browser detect)
- Quality selection 144p–4K + Audio Only, নিজস্ব scrollbar সহ ফিক্সড-হাইট বক্সে
- **ভিডিও কনটেইনার**: MP4 (ডিফল্ট), MKV, WebM, MOV, AVI — stream শুধু কপি হয়, re-encode নয়; তাই যে
  কনটেইনার ঐ কোয়ালিটির কোডেক ধরে না সেটা ধূসর দেখায় (WebM → VP9/AV1, MOV/AVI → H.264)
- **অডিও ফরম্যাট**: MP3 (128/192/256/320 kbps), M4A, Opus, **Original** (re-encode ছাড়া সাইটের নিজের অডিও),
  FLAC, WAV
- **Thumbnail embed** (কভার ছবি) ও **Metadata embed** (টাইটেল, আপলোডার, তারিখ, বর্ণনা, চ্যাপ্টার) —
  প্রতি ডাউনলোডে টিক দিয়ে, ডিফল্ট Settings → Output format-এ। ব্রাউজার এক্সটেনশনের ডাউনলোডেও Settings-এর ডিফল্ট লাগে।
  কভার বসে MP3, M4A, MP4/MOV, MKV, Opus, FLAC-এ; WebM, AVI, WAV-এ কভার রাখার জায়গা নেই (ডাউনলোড ঠিকই হয়, শুধু কভার বাদ)
- Subtitle ভাষা সিলেকশন (যা যা ভিডিওতে আসলেই আছে)
- Advanced options: custom filename, trim (start/end), playlist mode, output dir
- Download বাটন — কোয়ালিটি না বাছা পর্যন্ত dim/disabled, বাছলেই active
- পুরো নিচের প্যানেল (Advanced Options + Download বাটন) ফিক্সড, কখনো স্ক্রোল হয় না
- Queue পেজ: প্রতিটা ডাউনলোডের progress bar, speed/ETA, Pause/Resume/Cancel
- History পেজ: সম্পন্ন ডাউনলোডের লিস্ট, Open Folder, Clear History
- Settings পেজ: output dir, proxy, cookies file, speed limit, ffmpeg path, aria2c toggle
- About পেজ: SAIMUM HABIB + github.com/saimum10 লিংক, bundled tool status, feature list

## ⚠️ Pause/Resume নিয়ে একটা সততার নোট

yt-dlp-এ "আসল" pause নেই (মাঝপথে থামিয়ে ঠিক সেখান থেকে ধরে রাখার মতো লাইভ পজ না)।
এখানে Pause মানে আসলে: ডাউনলোড থামিয়ে দেওয়া হয় কিন্তু আংশিক ফাইল রেখে দেওয়া হয়,
আর Resume করলে yt-dlp-এর `continuedl` (এবং aria2c-এর নিজস্ব resume) ব্যবহার করে
সেই আংশিক ফাইল থেকেই আবার ডাউনলোড চালু হয়। বেশিরভাগ সাইটে এটা ঠিকঠাক কাজ করে,
কিন্তু কিছু সাইটে (যেমন expiring URL) resume মাঝে মাঝে শুরু থেকে শুরু করতে পারে।

## 📦 Build / packaging

`build_deb.sh` PyInstaller `--onedir` build তৈরি করে এবং `.deb` package সাজায়। Build-এর সময় FFmpeg/FFprobe binary সংগ্রহ ও package validation করা হয়। Extension-এর source files `browser_extension/`-এ থাকে এবং PyInstaller data হিসেবে bundle করা হয়.

### Linux ও Windows — একই কোড base

কোড একটাই; OS-ভেদে যা আলাদা (config/data ফোল্ডার, `.exe` নাম, autostart, প্রসেস বন্ধ করা,
কনসোল উইন্ডো লুকানো) সব `core/platform_utils.py` ও `core/autostart.py`-তে আছে।
Linux-এর ফোল্ডার/আচরণ আগের মতোই।

| | Linux | Windows |
|---|---|---|
| Settings | `~/.config/viddown` | `%APPDATA%\VidDown` |
| History / queue / cookies / log | `~/.local/share/viddown` | `%LOCALAPPDATA%\VidDown` |
| Autostart | `~/.config/autostart/*.desktop` | Registry: `HKCU\...\Run` |
| Build script | `build_deb.sh` → `.deb` | `build_windows.ps1` → Setup `.exe` + portable `.zip` |

### GitHub Actions (`.github/workflows/build.yml`)

- `main`/`master`-এ push বা **Actions → Run workflow** দিলে দুটো job চলে: `.deb` (Debian 12 container-এ) ও Windows (`windows-latest`-এ)। ফাইলগুলো run-এর **Artifacts** থেকে নামানো যায়।
- Build সফল হলে নিজে থেকেই **Releases**-এ `v<version.py-এর ভার্সন>` নামে release তৈরি হয় ও `.deb`, Setup `.exe`, portable `.zip` যুক্ত হয়। একই ভার্সনে আবার push করলে ওই release-এর ফাইল আপডেট হয়; নতুন release চাইলে `version.py` বদলান।
- Windows build-এ ffmpeg/ffprobe (BtbN, checksum যাচাই সহ) ও aria2c নিজে নামায়; `bin/`-এ হাতে কিছু রাখা লাগে না।
- Release দেওয়ার আগে `version.py`-এর ভার্সন বদলাতে ভুলবেন না — ফাইলের নাম সেখান থেকেই আসে।


## v2.9.5 — FFmpeg bundled-path fix

- Resolves ffmpeg across PyInstaller 5/6 onedir layouts and the system PATH.
- A stale custom FFmpeg Path no longer hides a valid bundled copy.
- Trim reports a clearer error when ffmpeg is genuinely unavailable.
- The Debian build now fails early if ffmpeg/ffprobe are missing from the packaged output.


## v2.9.6 — Download cancellation / fragment cleanup
- Added a Linux-only, narrowly matched aria2c watchdog that stops only the external downloader associated with the current output directory and output filename after cancellation.
- Removes temporary `.part`, fragment, `.ytdl`, and `.aria2` files reported for a cancelled job. Completed output files are not removed by this cleanup.
- Handles the race where yt-dlp returns just as the user cancels, preventing the job from being marked completed.
- Static syntax and package integrity checks passed; live-site cancellation testing is still required.


## v2.9.7 — Cancelled fragment cleanup
- Normalize yt-dlp temporary filenames before cleanup, fixing missed `.part-FragN` artifacts when hooks report a `.part` filename.
- Clean only job-associated partial/fragment artifacts; preserve normal final media files.
- Resume now waits for the previous worker to fully finish before re-queueing, preventing overlapping writers from creating duplicate outputs.

## v2.9.9 — Cancel loop + trim fixes
- **Cancel on part-by-part (HLS/DASH) downloads** no longer keeps looping through every remaining fragment: the progress hook now raises yt-dlp's `DownloadCancelled` instead of `DownloadError` (yt-dlp's fragment loop swallowed `DownloadError` and skipped to the next fragment).
- **Cancel / Pause / Quit now stop ffmpeg (trimmed downloads) and aria2c/curl children** — the exact processes each job's download thread started are tracked and terminated; their leftover `.part` files are cleaned up.
- **Trim range is part of the file name and job title** (`… [trim 00-01-30 to 00-02-45].mp4`), so a trimmed download can no longer be skipped as "already downloaded" because a full (or differently trimmed) file with the same name exists.
- Trimmed downloads show real progress/ETA (ffmpeg `-progress`), and a finished trimmed clip is removed if the job was cancelled.
- Trim validation: invalid times are rejected (no silent `00:00:00`), start past the video end is rejected, end is clamped to the video length, "whole video" = no trim, and a playlist asks before applying one trim range to every video.
- aria2c arguments are keyed to aria2c only (they used to leak into ffmpeg); `fetch_info()` no longer crashes without settings; aria2c matching works for symlinked download folders.
# VidDown

## 📦 দুই ধরনের build — JS runtime সহ / ছাড়া

YouTube-এর সব ফরম্যাট দেখাতে yt-dlp-এর একটা JavaScript runtime (Deno) আর `yt-dlp-ejs` লাগে।
তাই প্রতিটা প্ল্যাটফর্মে দুটো build তৈরি হয়:

| | with-js | no-js |
|---|---|---|
| Deno bundled | ✅ (প্যাকেজ বড়) | ❌ (ছোট; সিস্টেমে Deno/Node/Bun থাকলে সেটাই ব্যবহার হয়) |
| `.deb` | `viddown_<ver>-1_amd64_with-js.deb` | `viddown_<ver>-1_amd64_no-js.deb` |
| Windows Setup | `VidDown_Setup_<ver>_x64_with-js.exe` | `VidDown_Setup_<ver>_x64_no-js.exe` |
| Windows portable | `VidDown_<ver>_windows_x64_portable_with-js.zip` | `..._portable_no-js.zip` |

লোকালি বানাতে:

```bash
./build_deb.sh                      # with-js (ডিফল্ট)
./build_deb.sh --variant no-js
powershell -ExecutionPolicy Bypass -File .\build_windows.ps1 -Variant no-js
```

`DENO_VERSION=v2.x.y` দিয়ে Deno-র ভার্সন পিন করা যায় (ডিফল্ট: latest; checksum যাচাই হয়)।
দুটো build একই `viddown` প্যাকেজ — একটার ওপর অন্যটা ইনস্টল করলে বদলে যায়।
GitHub Actions দুই ভ্যারিয়েন্টই matrix-এ বানায় এবং release-এ `SHA256SUMS.txt` যোগ করে।

## 🔄 অ্যাপ আপডেট (GitHub থেকে)

**Settings → Updates & About → VidDown updates**-এ "Check for updates" বাটন আছে।
`github.com/saimum10/VidDown`-এর সর্বশেষ release দেখে নতুন ভার্সন থাকলে টুলবারে **Update to x.y.z** বাটন আসে।

- ডাউনলোড হওয়া ফাইল release-এর SHA-256-এর সাথে না মিললে ইনস্টল হয় না (checksum না থাকলেও না)।
- **Linux (.deb):** `pkexec` দিয়ে সিস্টেমের পাসওয়ার্ড ডায়ালগ আসে (পাসওয়ার্ড VidDown দেখে না); না থাকলে টার্মিনালে `sudo apt-get install --reinstall` খোলে।
- **Windows (Setup.exe):** installer silent মোডে চলে, অ্যাপ বন্ধ হয়ে নতুন ভার্সনে আবার চালু হয়। Portable zip-এ শুধু নতুন release আছে কি না জানায়।
- **Build type** বদলানো যায় (with-js ↔ no-js) — বাছাই করে আপডেট চাপলেই সুইচ হয়।
- Settings-এ "Check automatically" (ডিফল্ট চালু): স্টার্টের কিছুক্ষণ পর, ১২ ঘণ্টায় সর্বোচ্চ একবার।
- নতুন release আপডেটারে দেখাতে `version.py`-এর ভার্সন বাড়াতে হয় (ট্যাগ `v<ভার্সন>`)।

## ⚖️ লাইসেন্স

- VidDown নিজে **MIT License**-এ — দেখুন [`LICENSE`](LICENSE) (Copyright © 2024-2026 SAIMUM HABIB)।
- ব্যবহৃত তৃতীয়-পক্ষের টুলগুলো (yt-dlp, FFmpeg, aria2, Deno, Pillow …) নিজ নিজ লাইসেন্সে —
  তালিকা, লাইসেন্স ও সোর্সের লিংক [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)-এ।
- FFmpeg ও aria2 GPL; এগুলো আলাদা প্রোগ্রাম হিসেবে চালানো হয় (VidDown-এর সাথে link করা নয়)।
  `mutagen` (GPL) ইচ্ছে করেই build থেকে বাদ — থাম্বনেইল embed ffmpeg দিয়েই হয় (`core/cover_embed.py`),
  Opus/FLAC-এও, তাই mutagen লাগে না।
- প্যাকেজের ভেতরেও আছে: `.deb`-এ `/usr/share/doc/viddown/`, Windows-এ অ্যাপ ফোল্ডারে,
  আর অ্যাপে **Settings → Updates & About → Licenses**।
- নতুন টুল যোগ করলে `THIRD_PARTY_NOTICES.md`-ও হালনাগাদ করুন।
