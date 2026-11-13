"""Queueing a playlist: one list re-layout and one queue-file write for the
whole batch (not one per video), dialogs attached to the Add Download window,
and failures in the Download button reported instead of swallowed."""
import _setup  # noqa: F401  (must stay first)

import os
import unittest
from types import SimpleNamespace
from unittest import mock

try:
    import tkinter  # noqa: F401
    HAVE_TK = True
except ImportError:
    HAVE_TK = False


def _jobs(n):
    return [SimpleNamespace(id=f"j{i}", status="queued", title=f"T{i}", url="u") for i in range(n)]


@unittest.skipUnless(HAVE_TK, "needs tkinter")
class QueueBatchTests(unittest.TestCase):
    def _window(self):
        from ui.main_window import MainWindow
        mw = MainWindow.__new__(MainWindow)
        mw.all_jobs = {}
        mw.writes = 0
        mw._write_queue_state = lambda: setattr(mw, "writes", mw.writes + 1)
        mw.scheduled = []
        mw.after = lambda ms, fn: mw.scheduled.append(fn)
        return mw

    def test_queue_state_writes_are_coalesced(self):
        mw = self._window()
        for _ in range(100):
            mw._save_queue_state()
        self.assertEqual(len(mw.scheduled), 1, "one timer for 100 requests")
        self.assertEqual(mw.writes, 0)
        mw.scheduled.pop()()                       # the timer fires
        self.assertEqual(mw.writes, 1)
        mw._save_queue_state()                     # a later change schedules again
        self.assertEqual(len(mw.scheduled), 1)

    def test_now_writes_immediately_for_quit_paths(self):
        mw = self._window()
        mw._save_queue_state()
        mw._save_queue_state(now=True)
        self.assertEqual(mw.writes, 1)
        mw.scheduled.pop()()                       # the old timer finds nothing left to do
        self.assertEqual(mw.writes, 1)

    def test_quit_and_terminate_save_immediately(self):
        src = open(os.path.join(_setup.ROOT, "ui", "main_window.py"), encoding="utf-8").read()
        self.assertEqual(src.count("self._save_queue_state(now=True)"), 2)

    def test_queue_downloads_is_one_batch(self):
        mw = self._window()
        rows, started = [], []
        mw.pages = {"queue": SimpleNamespace(add_job_rows=lambda jobs: rows.append(list(jobs)))}
        mw.download_manager = SimpleNamespace(add_job=started.append)
        saves = []
        mw._save_queue_state = lambda now=False: saves.append(now)
        jobs = _jobs(200)
        mw.queue_downloads(jobs)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 200)
        self.assertEqual(len(started), 200)
        self.assertEqual(len(saves), 1)
        self.assertEqual(len(mw.all_jobs), 200)

    def test_restored_jobs_are_not_started(self):
        mw = self._window()
        started = []
        mw.pages = {"queue": SimpleNamespace(add_job_rows=lambda jobs: None)}
        mw.download_manager = SimpleNamespace(add_job=started.append)
        mw._save_queue_state = lambda now=False: None
        mw.queue_downloads(_jobs(3), start=False)
        self.assertEqual(started, [])
        self.assertEqual(len(mw.all_jobs), 3)

    def test_downloads_page_relayouts_once_per_batch(self):
        from ui.pages.downloads_page import DownloadsPage
        page = DownloadsPage.__new__(DownloadsPage)
        page.jobs = []
        calls = []
        page._relayout = lambda: calls.append(1)
        jobs = _jobs(50)
        page.add_job_rows(jobs)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(page.jobs), 50)
        self.assertIs(page.jobs[0].job, jobs[-1])   # newest first, same as add_job_row
        page.add_job_rows([])
        self.assertEqual(len(calls), 1)


@unittest.skipUnless(HAVE_TK, "needs tkinter")
class DownloadButtonTests(unittest.TestCase):
    def test_dialogs_get_the_add_download_window_as_parent(self):
        from ui.pages import download_page as dp
        top = object()
        mb = dp._parented_messagebox(top)
        with mock.patch.object(dp.messagebox, "askyesno", return_value=True) as ask, \
                mock.patch.object(dp.messagebox, "showwarning") as warn:
            self.assertTrue(mb.askyesno("t", "m"))
            mb.showwarning("t", "m")
        self.assertIs(ask.call_args.kwargs["parent"], top)
        self.assertIs(warn.call_args.kwargs["parent"], top)

    def test_a_failure_is_reported_not_swallowed(self):
        from ui.pages import download_page as dp
        page = dp.DownloadPage.__new__(dp.DownloadPage)
        page.winfo_toplevel = lambda: "TOP"
        page._start_download = mock.Mock(side_effect=RuntimeError("boom"))
        with mock.patch.object(dp.messagebox, "showerror") as err, \
                mock.patch.object(dp.errorlog, "error") as log:
            page._on_download_clicked()              # must not raise
        err.assert_called_once()
        self.assertEqual(err.call_args.kwargs["parent"], "TOP")
        self.assertIn("boom", err.call_args.args[1])
        log.assert_called_once()


class _Var:
    """Stand-in for a tk variable."""
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v

    def set(self, v):
        self.v = v


@unittest.skipUnless(HAVE_TK, "needs tkinter")
class DownloadHandlerEndToEndTests(unittest.TestCase):
    """Drive the real Download-button code with a fetched playlist / video.

    The page object is built WITHOUT running __init__ and without ever having
    shown a single-video preview card, which is exactly the state in which the
    button used to die with "'DownloadPage' object has no attribute
    '_title_entry'" (silently, because Tk swallows it)."""

    def _page(self, playlist, n=30):
        import tempfile
        from core import storage, downloader
        from ui.pages import download_page as dp
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        info = {"duration": 600, "formats": [
            {"format_id": "v", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "width": 1920,
             "height": 1080, "protocol": "https", "url": "u", "filesize": 50_000_000},
            {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "protocol": "https",
             "url": "u2", "filesize": 5_000_000}]}
        page = dp.DownloadPage.__new__(dp.DownloadPage)
        entries = [{"id": f"id{i}", "title": f"Video {i}", "url": f"https://www.youtube.com/watch?v=id{i}"}
                   for i in range(n)]
        page.current_info = {"title": "A list" if playlist else "One video", "entries": entries} if playlist \
            else {"title": "One video", "duration": 600}
        page.quality_options = downloader.build_quality_options(info)
        page.quality_var = _Var(0)
        page.edited_title = None
        page.url_var = _Var("https://www.youtube.com/watch?v=abc")
        page.output_dir_var = _Var(tmp)
        page.trim_enabled = _Var(False)
        page.sub_vars = {}
        page.embed_thumb_var = _Var(False)
        page.embed_meta_var = _Var(True)
        page.is_playlist = playlist
        page._playlist_entries = entries
        page._playlist_vars = [_Var(True) for _ in entries]
        page._info_url = "https://www.youtube.com/watch?v=abc"
        page._find_duplicate = lambda title: None
        page._notify_queued = lambda: None
        page.update_idletasks = lambda: None
        page.winfo_toplevel = lambda: "TOP"
        self.queued, self.singles, self.status = [], [], []
        page.app = SimpleNamespace(
            settings=dict(storage.DEFAULT_SETTINGS, output_dir=tmp),
            set_status=self.status.append,
            queue_downloads=lambda jobs: self.queued.extend(jobs),
            queue_download=self.singles.append)
        return page, dp

    def test_playlist_download_queues_every_selected_video(self):
        page, dp = self._page(playlist=True, n=30)
        with mock.patch.object(dp.messagebox, "showerror") as err:
            page._on_download_clicked()
        err.assert_not_called()
        self.assertEqual(len(self.queued), 30)
        self.assertEqual([j.base_title for j in self.queued][:2], ["Video 0", "Video 1"])
        self.assertIn("30 videos added to the queue.", self.status)

    def test_only_ticked_playlist_videos_are_queued(self):
        page, dp = self._page(playlist=True, n=10)
        for i in range(0, 10, 2):
            page._playlist_vars[i].set(False)
        with mock.patch.object(dp.messagebox, "showerror") as err:
            page._on_download_clicked()
        err.assert_not_called()
        self.assertEqual(len(self.queued), 5)

    def test_single_video_still_works(self):
        page, dp = self._page(playlist=False)
        with mock.patch.object(dp.messagebox, "showerror") as err:
            page._on_download_clicked()
        err.assert_not_called()
        self.assertEqual(len(self.singles), 1)

    def test_a_stale_destroyed_title_box_does_not_block_a_playlist(self):
        page, dp = self._page(playlist=True, n=3)
        page._title_entry = SimpleNamespace(winfo_exists=lambda: False)   # card was rebuilt
        with mock.patch.object(dp.messagebox, "showerror") as err:
            page._on_download_clicked()
        err.assert_not_called()
        self.assertEqual(len(self.queued), 3)
        self.assertIsNone(page._title_entry)

    def test_title_edit_widgets_have_class_level_defaults(self):
        from ui.pages import download_page as dp
        for name in ("_title_entry", "_title_label", "_title_edit_btn"):
            self.assertIsNone(getattr(dp.DownloadPage, name))


if __name__ == "__main__":
    unittest.main()
