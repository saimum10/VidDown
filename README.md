# VidDown (SAIMUM HABIB)

Standalone, self-contained ভিডিও ডাউনলোডার — Debian-based Linux এর জন্য।
GUI: Tkinter | Engine: yt-dlp | Bundled: ffmpeg, ffprobe, aria2c

#
| | with-js | no-js |
|---|---|---|
| Deno bundled | ✅ (প্যাকেজ বড়) | ❌ (ছোট; সিস্টেমে Deno/Node/Bun থাকলে সেটাই ব্যবহার হয়) |
| `.deb` | `viddown_<ver>-1_amd64_with-js.deb` | `viddown_<ver>-1_amd64_no-js.deb` |
| Windows Setup | `VidDown_Setup_<ver>_x64_with-js.exe` | `VidDown_Setup_<ver>_x64_no-js.exe` |
| Windows portable | `VidDown_<ver>_windows_x64_portable_with-js.zip` | `..._portable_no-js.zip` |


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
