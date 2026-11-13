import _setup  # noqa: F401  (must stay first)

import base64
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

from core import downloader, output_options as oo


# ---------------------------------------------------------------- tables ---
class AudioTableTests(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(oo.audio_label("mp3", 320), "Audio Only (MP3 320 kbps)")
        self.assertEqual(oo.audio_label("opus"), "Audio Only (Opus)")
        self.assertEqual(oo.audio_label("original"), "Audio Only (Original)")

    def test_unknown_values_fall_back(self):
        self.assertEqual(oo.normalize_audio_format("ogg"), "mp3")
        self.assertEqual(oo.normalize_audio_format(None), "mp3")
        self.assertEqual(oo.normalize_audio_bitrate("999"), 192)
        self.assertEqual(oo.normalize_audio_bitrate("256"), 256)
        self.assertEqual(oo.normalize_container("MKV"), "mkv")
        self.assertEqual(oo.normalize_container("flv"), "mp4")

    def test_sizes(self):
        self.assertEqual(oo.audio_size_bytes("mp3", 192, 100), 2_400_000)
        self.assertEqual(oo.audio_size_bytes("mp3", 320, 100), 4_000_000)
        self.assertEqual(oo.audio_size_bytes("wav", None, 10), 1_920_000)
        self.assertIsNone(oo.audio_size_bytes("mp3", 192, None))
        # copies of the source stream follow the source's size, not the duration
        self.assertEqual(oo.audio_size_bytes("opus", None, 100, source_bytes=1234), 1234)
        self.assertIsNone(oo.audio_size_bytes("m4a", None, 100))

    def test_extract_audio_postprocessor(self):
        self.assertEqual(oo.audio_ytdlp_postprocessor("mp3", 256),
                         {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "256"})
        # no quality for a copy: yt-dlp must not be told to re-encode
        self.assertEqual(oo.audio_ytdlp_postprocessor("opus"),
                         {"key": "FFmpegExtractAudio", "preferredcodec": "opus"})
        self.assertEqual(oo.audio_ytdlp_postprocessor("original")["preferredcodec"], "best")


class ContainerTableTests(unittest.TestCase):
    def test_choices_follow_available_codecs(self):
        got = {k: ok for k, _l, ok in oo.container_choices(["H.264"])}
        self.assertEqual(got, {"mp4": True, "mkv": True, "webm": False, "mov": True, "avi": True})
        got = {k: ok for k, _l, ok in oo.container_choices(["VP9", "AV1"])}
        self.assertEqual(got, {"mp4": True, "mkv": True, "webm": True, "mov": False, "avi": False})

    def test_chosen_codec_limits_containers(self):
        got = {k: ok for k, _l, ok in oo.container_choices(["H.264", "VP9", "AV1"], "AV1")}
        self.assertEqual(got, {"mp4": True, "mkv": True, "webm": True, "mov": False, "avi": False})
        got = {k: ok for k, _l, ok in oo.container_choices(["H.264", "VP9"], "H.264")}
        self.assertFalse(got["webm"])
        self.assertTrue(got["mov"])

    def test_subtitles(self):
        self.assertTrue(oo.container_supports_subtitles("mkv"))
        self.assertFalse(oo.container_supports_subtitles("avi"))
        self.assertEqual(oo.subtitle_format_for("webm"), "vtt")
        self.assertEqual(oo.subtitle_format_for("mp4"), "srt")

    def test_cover_support(self):
        for ext in ("mp3", "m4a", "opus", "flac", "mp4", "mkv"):
            self.assertTrue(oo.thumbnail_supported(ext), ext)
        for ext in ("webm", "wav", "avi"):
            self.assertFalse(oo.thumbnail_supported(ext), ext)
        self.assertFalse(oo.thumbnail_supported_for({"audio_only": True, "audio_format": "wav"}))
        self.assertTrue(oo.thumbnail_supported_for({"audio_only": True, "audio_format": "original"}))
        self.assertFalse(oo.thumbnail_supported_for({"container": "webm"}))
        self.assertTrue(oo.thumbnail_supported_for({}))          # default container is MP4


# ------------------------------------------------------------------ plan ---
def _keys(plan):
    return [pp["key"] for pp in plan["postprocessors"]]


class PlanTests(unittest.TestCase):
    def test_plain_mp4_download_is_unchanged(self):
        plan = oo.plan_postprocessing({"audio_only": False}, {})
        self.assertEqual(plan["merge_output_format"], "mp4")
        self.assertEqual(plan["postprocessors"], [])
        self.assertFalse(plan["writethumbnail"])

    def test_mkv_remuxes_single_files_too(self):
        plan = oo.plan_postprocessing({"container": "mkv"}, {})
        self.assertEqual(plan["merge_output_format"], "mkv")
        self.assertEqual(plan["postprocessors"], [{"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"}])

    def test_order_remux_subtitles_metadata(self):
        plan = oo.plan_postprocessing({"container": "mkv"},
                                      {"subtitles": ["en"], "embed_metadata": True, "embed_thumbnail": True})
        self.assertEqual(_keys(plan), ["FFmpegVideoRemuxer", "FFmpegSubtitlesConvertor",
                                       "FFmpegEmbedSubtitle", "FFmpegMetadata"])
        self.assertTrue(plan["writethumbnail"] and plan["embed_thumbnail"])

    def test_webm_takes_vtt_subtitles(self):
        plan = oo.plan_postprocessing({"container": "webm"}, {"subtitles": ["en"]})
        self.assertEqual(plan["subtitle_format"], "vtt")
        self.assertEqual(plan["postprocessors"][1], {"key": "FFmpegSubtitlesConvertor", "format": "vtt"})

    def test_avi_skips_subtitles_and_says_so(self):
        plan = oo.plan_postprocessing({"container": "avi"}, {"subtitles": ["en"]})
        self.assertEqual(plan["subtitles"], [])
        self.assertNotIn("FFmpegEmbedSubtitle", _keys(plan))
        self.assertTrue(any("subtitles" in n for n in plan["notes"]))

    def test_cover_skipped_where_impossible(self):
        plan = oo.plan_postprocessing({"container": "webm"}, {"embed_thumbnail": True})
        self.assertFalse(plan["writethumbnail"])
        self.assertFalse(plan["embed_thumbnail"])
        self.assertTrue(plan["notes"])

    def test_audio_plan(self):
        plan = oo.plan_postprocessing({"audio_only": True, "audio_format": "opus"},
                                      {"embed_metadata": True, "embed_thumbnail": True, "subtitles": ["en"]})
        self.assertIsNone(plan["merge_output_format"])
        self.assertEqual(_keys(plan), ["FFmpegExtractAudio", "FFmpegMetadata"])
        self.assertEqual(plan["subtitles"], [])
        self.assertTrue(plan["embed_thumbnail"])

    def test_old_saved_jobs_still_plan(self):
        """A queue saved by an older version has neither container nor audio_format."""
        plan = oo.plan_postprocessing({"audio_only": True}, {})
        self.assertEqual(plan["postprocessors"],
                         [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}])
        self.assertEqual(oo.plan_postprocessing({}, {})["merge_output_format"], "mp4")


# --------------------------------------------- quality options (downloader) --
def _fmt(fid, ext, vcodec, acodec, height=None, size=None, tbr=None):
    return {"format_id": fid, "ext": ext, "vcodec": vcodec, "acodec": acodec, "height": height,
            "filesize": size, "tbr": tbr, "protocol": "https", "format_note": "",
            "width": height * 16 // 9 if height else None}


def fake_info():
    mb = 1_000_000
    # worst -> best, the order yt-dlp hands formats over in (it picks the last match)
    return {"title": "T", "duration": 200, "formats": [
        _fmt("140", "m4a", "none", "mp4a.40.2", size=3 * mb, tbr=128),
        _fmt("251", "webm", "none", "opus", size=2 * mb, tbr=130),
        _fmt("136", "mp4", "avc1.4d401f", "none", 720, 20 * mb, 2000),
        _fmt("137", "mp4", "avc1.640028", "none", 1080, 40 * mb, 4000),
        _fmt("248", "webm", "vp09.00.40.08", "none", 1080, 25 * mb, 2500),
        _fmt("399", "mp4", "av01.0.08M.08", "none", 1080, 15 * mb, 1500),
    ]}


class QualityOptionTests(unittest.TestCase):
    def setUp(self):
        self.options = downloader.build_quality_options(fake_info())
        self.v1080 = next(o for o in self.options if o.get("height") == 1080)
        self.v720 = next(o for o in self.options if o.get("height") == 720)
        self.audio = self.options[-1]

    def test_audio_option_defaults_to_mp3_192(self):
        self.assertTrue(self.audio["audio_only"])
        self.assertEqual(self.audio["label"], "Audio Only (MP3 192 kbps)")
        self.assertEqual(self.audio["size_bytes"], 200 * 192_000 // 8)

    def test_audio_formats(self):
        opus = downloader.apply_audio_format(self.audio, "opus")
        self.assertEqual(opus["audio_format"], "opus")
        self.assertEqual(opus["label"], "Audio Only (Opus)")
        self.assertIn("acodec=opus", opus["format_selector"])
        self.assertEqual(opus["size_bytes"], 2_000_000)           # the Opus stream itself
        m4a = downloader.apply_audio_format(self.audio, "m4a")
        self.assertEqual(m4a["size_bytes"], 3_000_000)
        mp3 = downloader.apply_audio_format(self.audio, "mp3", 320)
        self.assertEqual(mp3["size_bytes"], 200 * 320_000 // 8)
        self.assertEqual(mp3["audio_bitrate"], 320)
        self.assertEqual(self.audio["audio_format"], "mp3")       # the original is not modified
        # other qualities are left alone
        self.assertIs(downloader.apply_audio_format(self.v720, "opus"), self.v720)

    def test_default_container_selector_is_unchanged(self):
        self.assertEqual(self.v1080["container"], "mp4")
        self.assertEqual(self.v1080["format_selector"],
                         "bestvideo[height<=1080]+bestaudio/best[height<=1080]")

    def test_mkv_keeps_the_same_streams(self):
        mkv = downloader.apply_container(self.v1080, "mkv")
        self.assertEqual(mkv["container"], "mkv")
        self.assertEqual(mkv["format_selector"], self.v1080["format_selector"])

    def test_webm_picks_vp9_with_opus(self):
        webm = downloader.apply_container(self.v1080, "webm")
        self.assertEqual(webm["container"], "webm")
        self.assertIn("vp9|vp09", webm["format_selector"])
        self.assertIn("acodec~='^(opus|vorbis)'", webm["format_selector"])
        self.assertEqual(webm["size_bytes"], 25_000_000 + 2_000_000)   # VP9 + the Opus stream

    def test_mov_picks_h264_with_aac(self):
        mov = downloader.apply_container(self.v1080, "mov")
        self.assertEqual(mov["container"], "mov")
        self.assertIn("avc1|h264", mov["format_selector"])
        self.assertIn("acodec~='^mp4a'", mov["format_selector"])
        self.assertEqual(mov["size_bytes"], 40_000_000 + 3_000_000)

    def test_impossible_container_falls_back_to_mp4(self):
        # 720p here only has H.264, which WebM cannot hold
        webm = downloader.apply_container(self.v720, "webm")
        self.assertEqual(webm["container"], "mp4")

    def test_codec_and_container_work_together(self):
        av1 = downloader.apply_codec(self.v1080, "AV1")
        webm = downloader.apply_container(av1, "webm")
        self.assertEqual((webm["codec"], webm["container"]), ("AV1", "webm"))
        self.assertIn("av01", webm["format_selector"])
        # choosing H.264 afterwards cannot stay WebM
        h264 = downloader.apply_codec(webm, "H.264")
        self.assertEqual((h264["codec"], h264["container"]), ("H.264", "mp4"))
        # ... and going back to Auto restores the normal selector
        auto = downloader.apply_codec(av1, "Auto")
        self.assertEqual(auto["format_selector"], self.v1080["format_selector"])

    def test_direct_link_option_has_no_container_picker(self):
        info = {"formats": [_fmt("0", "mp4", "avc1", "mp4a", None)]}
        opts = downloader.build_quality_options(info)
        original = opts[0]
        self.assertIs(downloader.apply_container(original, "mkv"), original)

    def test_defaults_applied_to_every_option(self):
        options = downloader.build_quality_options(fake_info())
        downloader.apply_default_outputs(options, {"default_audio_format": "m4a", "default_container": "mkv"})
        self.assertEqual(options[-1]["label"], "Audio Only (M4A)")
        self.assertTrue(all(o["container"] == "mkv" for o in options if o.get("codecs")))


class JobPersistenceTests(unittest.TestCase):
    def test_round_trip_keeps_the_new_options(self):
        options = downloader.build_quality_options(fake_info())
        quality = downloader.apply_container(options[0], "mkv")
        job = downloader.DownloadJob("https://x/y", "T", downloader.job_quality(quality), {"output_dir": "."},
                                     embed_thumbnail=True, embed_metadata=True)
        back = downloader.deserialize_job(downloader.serialize_job(job))
        self.assertEqual(back.quality["container"], "mkv")
        self.assertTrue(back.options["embed_thumbnail"])
        self.assertTrue(back.options["embed_metadata"])

        audio = downloader.apply_audio_format(options[-1], "opus")
        job = downloader.DownloadJob("https://x/y", "T", downloader.job_quality(audio), {"output_dir": "."})
        back = downloader.deserialize_job(downloader.serialize_job(job))
        self.assertEqual(back.quality["audio_format"], "opus")
        self.assertFalse(back.options["embed_thumbnail"])

    def test_queued_job_is_not_changed_by_later_picks(self):
        option = downloader.build_quality_options(fake_info())[0]
        job = downloader.DownloadJob("https://x/y", "T", downloader.job_quality(option), {"output_dir": "."})
        option.update(downloader.apply_container(option, "mov"))
        self.assertEqual(job.quality["container"], "mp4")

    def test_old_saved_queue_without_new_keys(self):
        data = {"url": "https://x", "title": "T", "settings": {"output_dir": "."},
                "quality": {"height": 0, "label": "Audio Only (MP3)", "size_bytes": None, "size_label": "",
                            "format_selector": "bestaudio/best", "audio_only": True, "codec": None},
                "options": {"subtitles": [], "custom_filename": "", "trim": None}}
        job = downloader.deserialize_job(data)
        plan = oo.plan_postprocessing(job.quality, job.options)
        self.assertEqual(plan["postprocessors"][0]["preferredcodec"], "mp3")


class ThumbnailCleanupTests(unittest.TestCase):
    def test_sibling_names(self):
        got = downloader._thumbnail_siblings([
            "/d/Title [720p].f137.mp4.part",
            "/d/Title [720p].f251.webm.part-Frag3.aria2",
            "/d/Title [720p].mp4",
        ])
        self.assertIn("/d/Title [720p].webp", got)
        self.assertIn("/d/Title [720p].jpg", got)
        self.assertEqual({os.path.dirname(p) for p in got}, {"/d"})
        self.assertFalse(any(".f137" in p or ".f251" in p for p in got))

    def test_cancel_removes_the_cover_only_when_embedding(self):
        with tempfile.TemporaryDirectory() as d:
            thumb = os.path.join(d, "Title [720p].webp")
            other = os.path.join(d, "Unrelated.webp")
            for p in (thumb, other):
                open(p, "wb").close()
            job = downloader.DownloadJob("https://x", "T", {"audio_only": False}, {"output_dir": d},
                                         embed_thumbnail=True)
            job._artifact_paths.add(os.path.join(d, "Title [720p].f137.mp4.part"))
            downloader._discard_partial_files(job)
            self.assertFalse(os.path.exists(thumb))
            self.assertTrue(os.path.exists(other))

            open(thumb, "wb").close()
            job = downloader.DownloadJob("https://x", "T", {"audio_only": False}, {"output_dir": d})
            job._artifact_paths.add(os.path.join(d, "Title [720p].f137.mp4.part"))
            downloader._discard_partial_files(job)
            self.assertTrue(os.path.exists(thumb))        # not embedding: not this job's file


# ------------------------------------------------------------ cover embedding --
@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class CoverEmbedTests(unittest.TestCase):
    """Real ffmpeg, no network: every format the app offers must either get its cover
    or be skipped without an error — and never need the GPL `mutagen` package."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.tmp, True)
        cls.cover = os.path.join(cls.tmp, "cover.jpg")
        cls._ff("-f", "lavfi", "-i", "color=c=red:s=320x180", "-frames:v", "1", cls.cover)

    @staticmethod
    def _ff(*args):
        subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)

    def _make(self, ext, *codec_args):
        path = os.path.join(self.tmp, f"clip.{ext}")
        self._ff("-f", "lavfi", "-i", "sine=duration=1", *codec_args, path)
        return path

    def _streams(self, path):
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                              "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
        return out.split()

    def _embed(self, path, ext):
        import yt_dlp
        from core import cover_embed
        thumb = os.path.join(self.tmp, f"thumb_{ext}.jpg")
        shutil.copy(self.cover, thumb)
        info = {"ext": ext, "filepath": path, "thumbnails": [{"filepath": thumb}], "__files_to_move": {}}
        with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
            pp = cover_embed.SafeEmbedThumbnailPP(ydl)
            warnings = []
            pp.report_warning = warnings.append
            pp.run(info)
        self.assertFalse(os.path.exists(thumb), "the thumbnail file must be cleaned up")
        return warnings

    def test_formats_that_take_a_cover(self):
        for ext, args in (("mp3", ("-c:a", "libmp3lame")), ("m4a", ("-c:a", "aac")),
                          ("opus", ("-c:a", "libopus")), ("flac", ("-c:a", "flac"))):
            with self.subTest(ext=ext):
                path = self._make(ext, *args)
                self.assertEqual(self._streams(path), ["audio"])
                self.assertEqual(self._embed(path, ext), [])
                self.assertEqual(self._streams(path), ["audio", "video"])   # the picture

    def test_formats_without_a_cover_are_skipped_quietly(self):
        path = self._make("wav")
        self.assertEqual(self._embed(path, "wav"), [])
        self.assertEqual(self._streams(path), ["audio"])


class CoverHelpersTests(unittest.TestCase):
    def test_flac_picture_block_layout(self):
        from core.cover_embed import flac_picture_block, image_size
        jpg = b"\xff\xd8\xff\xc0\x00\x11\x08\x00\xb4\x01\x40\x03" + b"\x00" * 20
        self.assertEqual(image_size(jpg), (320, 180))
        block = flac_picture_block(jpg)
        ptype, mlen = struct.unpack(">II", block[:8])
        self.assertEqual((ptype, block[8:8 + mlen]), (3, b"image/jpeg"))
        self.assertTrue(block.endswith(jpg))
        w, h = struct.unpack(">II", block[8 + mlen + 4:8 + mlen + 12])
        self.assertEqual((w, h), (320, 180))

    def test_ffmetadata_is_decodable(self):
        from core.cover_embed import flac_picture_block, vorbis_cover_ffmetadata
        data = bytes(range(256)) * 4
        text = vorbis_cover_ffmetadata(data)
        self.assertTrue(text.startswith(";FFMETADATA1\nMETADATA_BLOCK_PICTURE="))
        value = text.split("=", 1)[1].rstrip("\n").replace("\\=", "=")
        self.assertEqual(base64.b64decode(value), flac_picture_block(data))


if __name__ == "__main__":
    unittest.main()
