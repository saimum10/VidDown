"""End-to-end checks with a real local HTTP server + ffmpeg:
- the job's progress never runs backwards when video and audio are merged
- a paused job keeps its partial files, a cancelled one loses them"""
import _setup  # noqa: F401  (must stay first)

import functools
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock

try:
    import yt_dlp
    from core import downloader, storage
except ImportError:
    downloader = None

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipIf(downloader is None or not HAVE_FFMPEG, "needs yt-dlp and ffmpeg")
class MergedProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.srv_dir = os.path.join(cls.tmp, "srv")
        os.makedirs(cls.srv_dir)
        run = lambda *a: subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)
        run("-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=6", "-c:v", "libx264",
            "-b:v", "3M", "-pix_fmt", "yuv420p", os.path.join(cls.srv_dir, "v.mp4"))
        run("-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:a", "aac", "-b:a", "256k",
            os.path.join(cls.srv_dir, "a.m4a"))
        handler = functools.partial(_Quiet, directory=cls.srv_dir)
        cls.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _info(self):
        base = f"http://127.0.0.1:{self.port}"
        return {"id": "t", "title": "merged", "ext": "mp4", "extractor": "x", "extractor_key": "X",
                "webpage_url": base + "/",
                "formats": [
                    {"format_id": "v", "url": base + "/v.mp4", "ext": "mp4", "vcodec": "avc1",
                     "acodec": "none", "height": 360, "width": 640, "protocol": "http"},
                    {"format_id": "a", "url": base + "/a.m4a", "ext": "m4a", "vcodec": "none",
                     "acodec": "mp4a", "protocol": "http"}]}

    def test_progress_never_goes_backwards_for_video_plus_audio(self):
        history = []

        class J(downloader.DownloadJob):
            @property
            def progress(self):
                return self.__dict__.get("_p", 0.0)

            @progress.setter
            def progress(self, v):
                self.__dict__["_p"] = v
                history.append(v)

        out = os.path.join(self.tmp, "out")
        settings = dict(storage.DEFAULT_SETTINGS, output_dir=out, use_cookies=False, aria2c_enabled=False,
                        embed_metadata=False, embed_thumbnail=False)
        quality = {"height": 360, "label": "360p", "format_selector":
                   "bestvideo[height<=360]+bestaudio/best[height<=360]", "audio_only": False,
                   "codec": "Auto", "container": "mp4"}
        job = J("http://127.0.0.1/x", "merged", quality, settings)
        real = yt_dlp.YoutubeDL.download

        def fake_download(self_, urls):
            self_.process_ie_result(self._info(), download=True)
            return 0

        mgr = downloader.DownloadManager(on_update=lambda j: None, max_concurrent=1, live_settings=settings)
        with mock.patch.object(yt_dlp.YoutubeDL, "download", fake_download):
            with mgr._lock:
                mgr.active_jobs[job.id] = job
                job.status = "downloading"
            mgr._run_job(job)
        self.assertEqual(job.status, "completed", job.error if hasattr(job, "error") else "")
        drops = [(a, b) for a, b in zip(history, history[1:]) if b < a - 0.01 and b != 0.0]
        # (a reset to 0 at the very start of a run is fine; a fall mid-way is the bug)
        self.assertEqual(drops, [], f"progress went backwards: {drops} in {history}")
        self.assertTrue(any(v >= 90 for v in history))


@unittest.skipIf(downloader is None, "needs yt-dlp")
class PauseVsCancelFilesTests(unittest.TestCase):
    def _run(self, stop):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        part = os.path.join(tmp, "clip.mp4.part")
        with open(part, "wb") as fh:
            fh.write(b"x" * 1000)
        settings = dict(storage.DEFAULT_SETTINGS, output_dir=tmp)
        job = downloader.DownloadJob("http://x/", "clip", {"label": "360p", "height": 360,
                                                            "format_selector": "best"}, settings)
        job._artifact_paths.add(part)
        job._owned_paths.add(part)
        mgr = downloader.DownloadManager(on_update=lambda j: None, max_concurrent=1, live_settings=settings)

        def fake_inner(job_):
            job_._stop_flag.wait(5)
            mgr._conclude(job_, "error", "stopped")

        with mock.patch.object(mgr, "_run_job_inner", fake_inner):
            with mgr._lock:
                mgr.active_jobs[job.id] = job
                job.status = "downloading"
            t = threading.Thread(target=mgr._run_job, args=(job,))
            t.start()
            stop(job)
            t.join(10)
        return job, part

    def test_pause_keeps_partial_file(self):
        job, part = self._run(lambda j: j.pause())          # what Terminate now does
        self.assertTrue(os.path.exists(part))
        self.assertNotEqual(job.status, "cancelled")

    def test_cancel_removes_partial_file(self):
        job, part = self._run(lambda j: j.cancel())         # what Terminate used to do
        self.assertFalse(os.path.exists(part))


@unittest.skipIf(downloader is None, "needs yt-dlp")
class DismissFailedJobTests(unittest.TestCase):
    """'Clear failed' / 'Remove from list' on an errored job must delete the
    partial files that job created (Retry, which does not cancel, keeps them)."""

    def _failed_job(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        owned = os.path.join(tmp, "clip.f137.mp4.part")
        foreign = os.path.join(tmp, "someone-elses.mp4")        # not created by this job
        for path in (owned, foreign):
            with open(path, "wb") as fh:
                fh.write(b"x" * 100)
        settings = dict(storage.DEFAULT_SETTINGS, output_dir=tmp)
        job = downloader.DownloadJob("http://x/", "clip", {"label": "360p", "height": 360,
                                                            "format_selector": "best"}, settings)
        job._artifact_paths.update({owned, foreign})
        job._owned_paths.add(owned)
        job.status = "error"
        mgr = downloader.DownloadManager(on_update=lambda j: None, max_concurrent=1, live_settings=settings)
        return mgr, job, owned, foreign

    def _wait_gone(self, path):
        import time
        for _ in range(100):
            if not os.path.exists(path):
                return True
            time.sleep(0.05)
        return False

    def test_dismissing_an_errored_job_removes_its_partial_file_only(self):
        mgr, job, owned, foreign = self._failed_job()
        self.assertTrue(mgr.cancel(job))
        self.assertTrue(self._wait_gone(owned), "partial file of the dismissed job is still there")
        self.assertTrue(os.path.exists(foreign), "a file the job did not create must never be deleted")

    def test_retry_does_not_cancel_the_old_job(self):
        """Retry must not go through manager.cancel(), or it would delete the
        partial data it is about to resume from."""
        import re
        src = open(os.path.join(_setup.ROOT, "ui", "pages", "downloads_page.py"), encoding="utf-8").read()
        body = src[src.index("    def retry_item"):src.index("    def pause_selection_or_all")]
        self.assertNotRegex(body, r"cancel")


if __name__ == "__main__":
    unittest.main()
