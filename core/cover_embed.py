"""
core/cover_embed.py
Puts the video's thumbnail into the finished file as its cover picture.

yt-dlp has its own EmbedThumbnail step, but it
  * needs the `mutagen` package (GPL) for .opus / .ogg / .flac and, optionally,
    .m4a — VidDown deliberately does not ship it (see THIRD_PARTY_NOTICES.md), and
  * raises an error when it cannot embed, which would fail a download that
    already finished just because of a decoration.

SafeEmbedThumbnailPP wraps it:
  * mp3 / m4a / mp4 / mov / mkv   → yt-dlp's own ffmpeg-based path;
  * flac                          → ffmpeg, picture as an "attached pic" stream;
  * opus / ogg                    → ffmpeg, picture as a METADATA_BLOCK_PICTURE
                                    Vorbis comment (what players read), handed
                                    over in a small metadata file so a large
                                    picture never hits the command-line limit;
  * anything else, or any failure → a warning; the download still succeeds.
The thumbnail file is always removed afterwards.

Imports yt-dlp, so import this module only where yt-dlp is already in use.
"""
import base64
import os
import struct

from yt_dlp.postprocessor.embedthumbnail import EmbedThumbnailPP
from yt_dlp.postprocessor.ffmpeg import FFmpegThumbnailsConvertorPP
from yt_dlp.utils import DownloadCancelled, prepend_extension

from core.output_options import thumbnail_supported

_FFMPEG_AUDIO_EXTS = ("opus", "ogg", "flac")


# ------------------------------------------------------- pure helpers -------
def image_size(data):
    """(width, height) of a JPEG or PNG, or (0, 0) when it cannot be read."""
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", data[16:24])
        if data[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                length = struct.unpack(">H", data[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    height, width = struct.unpack(">HH", data[i + 5:i + 9])
                    return width, height
                i += 2 + length
    except (struct.error, IndexError):
        pass
    return 0, 0


def mime_for(data):
    return "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"


def flac_picture_block(data, mime=None):
    """A FLAC METADATA_BLOCK_PICTURE (front cover) holding `data`."""
    mime = (mime or mime_for(data)).encode("ascii")
    width, height = image_size(data)
    return (struct.pack(">I", 3)                      # picture type 3 = front cover
            + struct.pack(">I", len(mime)) + mime
            + struct.pack(">I", 0)                    # no description
            + struct.pack(">IIII", width, height, 24, 0)
            + struct.pack(">I", len(data)) + data)


def _ffmeta_escape(text):
    for ch in ("\\", "=", ";", "#", "\n"):
        text = text.replace(ch, "\\" + ch)
    return text


def vorbis_cover_ffmetadata(data):
    """ffmpeg 'ffmetadata' file text that adds `data` as the cover of an Ogg file."""
    block = base64.b64encode(flac_picture_block(data)).decode("ascii")
    return ";FFMETADATA1\nMETADATA_BLOCK_PICTURE=" + _ffmeta_escape(block) + "\n"


# --------------------------------------------------------- the postprocessor --
class SafeEmbedThumbnailPP(EmbedThumbnailPP):
    """EmbedThumbnail that never needs mutagen and never fails the download."""

    @staticmethod
    def _thumbnail_paths(info):
        return [t["filepath"] for t in (info.get("thumbnails") or [])
                if t.get("filepath") and os.path.exists(t["filepath"])]

    def _discard_thumbnails(self, info, extra=()):
        if self._already_have_thumbnail:
            return
        for path in (*self._thumbnail_paths(info), *extra):
            try:
                os.remove(path)
            except OSError:
                pass

    def _as_jpeg(self, info):
        """Make the newest thumbnail a JPEG (small, and every player reads it).
        Returns the paths that became redundant."""
        thumbs = info.get("thumbnails") or []
        idx = next((i for i in range(len(thumbs) - 1, -1, -1)
                    if thumbs[i].get("filepath") and os.path.exists(thumbs[i]["filepath"])), None)
        if idx is None:
            return []
        convertor = FFmpegThumbnailsConvertorPP(self._downloader)
        try:
            convertor.fixup_webp(info, idx)
        except KeyError:
            pass
        path = thumbs[idx]["filepath"]
        if os.path.splitext(path)[1].lower() in (".jpg", ".jpeg"):
            return []
        converted = convertor.convert_thumbnail(path, "jpg")
        thumbs[idx]["filepath"] = converted
        return [path]

    def _newest_thumbnail(self, info):
        paths = self._thumbnail_paths(info)
        return paths[-1] if paths else None

    def _embed_with_ffmpeg(self, info, ext):
        filename = info["filepath"]
        thumb = self._newest_thumbnail(info)
        if not thumb:
            return
        temp = prepend_extension(filename, "temp")
        mtime = os.stat(filename).st_mtime
        self.to_screen(f'ffmpeg: Adding thumbnail to "{filename}"')
        if ext == "flac":
            self.run_ffmpeg_multiple_files(
                [filename, thumb], temp,
                ["-map", "0:a", "-map", "1:v", "-c", "copy",
                 "-disposition:v:0", "attached_pic",
                 "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
        else:   # opus / ogg
            with open(thumb, "rb") as f:
                data = f.read()
            meta = prepend_extension(filename, "cover") + ".ffmeta"
            try:
                with open(meta, "w", encoding="ascii") as f:
                    f.write(vorbis_cover_ffmetadata(data))
                self.real_run_ffmpeg(
                    [(filename, []), (meta, ["-f", "ffmetadata"])],
                    [(temp, ["-map", "0:a", "-map_metadata", "0", "-map_metadata", "1", "-c", "copy"])])
            finally:
                try:
                    os.remove(meta)
                except OSError:
                    pass
        os.replace(temp, filename)
        self.try_utime(filename, mtime, mtime)

    def run(self, info):
        ext = (info.get("ext") or "").lower()
        leftovers = []
        try:
            if not self._thumbnail_paths(info):
                self.to_screen("There is no thumbnail to embed")
                return [], info
            if not thumbnail_supported(ext):
                self.to_screen(f"A cover picture cannot be stored in .{ext} files; skipping it")
            else:
                leftovers = self._as_jpeg(info)
                if ext in _FFMPEG_AUDIO_EXTS:
                    self._embed_with_ffmpeg(info, ext)
                else:
                    super().run(info)
        except DownloadCancelled:
            raise
        except Exception as err:   # a missing cover must never fail a finished download
            self.report_warning(f"Could not embed the thumbnail: {err}")
        self._discard_thumbnails(info, leftovers)
        return [], info


def attach(ydl):
    """Register the embedding step; call after YoutubeDL() is created. It runs
    after yt-dlp's own steps (audio extraction, metadata), which is what a
    cover should see."""
    ydl.add_post_processor(SafeEmbedThumbnailPP(ydl), when="post_process")
