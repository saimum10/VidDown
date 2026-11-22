#!/usr/bin/env python3
"""
tools/newpipe_sizes_debug.py
Shows where every quality's size comes from with the NewPipe engine, step by step:

  1. what the helper said about each stream (height, codec, bitrate, exact length),
  2. what is in each stream's address (clen=),
  3. what the server says (HEAD, then a one-byte Range request),
  4. the size the quality list finally shows.

usage:  python tools/newpipe_sizes_debug.py "https://www.youtube.com/watch?v=VIDEO_ID"

Paste the whole output when reporting a size problem. Stream addresses are cut short
(they are signed links); nothing here is sent anywhere.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import downloader as dl          # noqa: E402
from core import newpipe_engine as npe     # noqa: E402


def _short(url):
    return (url or "")[:60] + ("..." if url and len(url) > 60 else "")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    print("helper:", npe.find_native_helper() or npe.find_helper_jar() or "NOT FOUND")
    info = npe.fetch(url, {}, force_noplaylist=True)
    formats = info.get("formats") or []
    print(f"\n{len(formats)} streams, duration {info.get('duration')} s\n")

    import yt_dlp
    print(f"{'id':8} {'h':>5} {'vcodec':14} {'tbr':>8} {'helper len':>12} {'clen in url':>12} "
          f"{'HEAD':>12} {'Range':>12}  url")
    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "socket_timeout": 8}) as ydl:
        from yt_dlp.networking import Request
        for f in formats:
            head = rng = None
            try:
                with ydl.urlopen(Request(f["url"], method="HEAD")) as r:
                    head = r.headers.get("Content-Length")
            except Exception as exc:
                head = f"ERR {type(exc).__name__}"
            try:
                with ydl.urlopen(Request(f["url"], headers={"Range": "bytes=0-0"})) as r:
                    rng = r.headers.get("Content-Range") or ("200 " + str(r.headers.get("Content-Length")))
            except Exception as exc:
                rng = f"ERR {type(exc).__name__}"
            print(f"{f.get('format_id', ''):8} {str(f.get('height') or ''):>5} "
                  f"{str(f.get('vcodec') or '')[:14]:14} {str(f.get('tbr') or ''):>8} "
                  f"{str(f.get('filesize') or ''):>12} {str(dl._clen_from_url(f['url']) or ''):>12} "
                  f"{str(head):>12} {str(rng):>12}  {_short(f['url'])}")

    dl.fill_exact_sizes(info, {})
    print("\nafter fill_exact_sizes:")
    for f in formats:
        print(f"  {f.get('format_id', ''):8} filesize={f.get('filesize')}  tbr={f.get('tbr')}")
    print("\nquality list:")
    for o in dl.build_quality_options(info):
        print(f"  {o['label']:28} {o['size_label']:>12}   selector: {o['format_selector']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
