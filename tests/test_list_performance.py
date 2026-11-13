"""The downloads list: rows are kept (not rebuilt) when tabs change, long lists are built in
slices, and a progress update does the minimum work."""
import _setup  # noqa: F401  (must stay first)

import random
import time
import tkinter as tk
import unittest

try:
    from theme import init_fonts, load_theme
    from core import storage
    from core.downloader import DownloadJob
    from ui.pages import downloads_page as dp
except ImportError:           # pragma: no cover
    dp = None


class _App:
    """Just what a DownloadsPage asks of the main window."""
    def __init__(self):
        self.history = []
        self.settings = dict(storage.DEFAULT_SETTINGS)
        self.summary_updates = 0

    def on_downloads_changed(self):
        pass

    def schedule_summary_update(self):
        self.summary_updates += 1


_Q = {"height": 720, "label": "720p (HD)", "format_selector": "best", "audio_only": False,
      "codec": "Auto", "container": "mp4", "size_bytes": 5_000_000, "size_label": "5 MB"}


def make_jobs(n, start=0):
    s = dict(storage.DEFAULT_SETTINGS, output_dir="/tmp")
    return [DownloadJob(f"https://example.com/v/{i}", f"Video number {i} with a fairly long title for the row",
                        dict(_Q), dict(s)) for i in range(start, start + n)]


@unittest.skipIf(dp is None, "needs the app modules")
class ListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # ONE Tk root for the whole class: icons and widget pictures are cached against the
        # root that first drew them, so a fresh root per test would meet pictures from a dead one.
        try:
            cls.root = tk.Tk()
        except tk.TclError:
            raise unittest.SkipTest("no display for Tk")
        cls.root.geometry("900x700+0+0")
        load_theme("dark")
        init_fonts(cls.root)

    @classmethod
    def tearDownClass(cls):
        _setup.destroy_tk(cls.root)

    def setUp(self):
        self.app = _App()
        self.page = dp.DownloadsPage(self.root, self.app)
        self.page.pack(fill="both", expand=True)
        self.root.update()
        self._consts = (dp.MAX_HIDDEN_ROWS, dp.FIRST_BATCH, dp.BATCH_MS)

    def tearDown(self):
        dp.MAX_HIDDEN_ROWS, dp.FIRST_BATCH, dp.BATCH_MS = self._consts
        self.page._cancel_build()
        if self.page._trim_after is not None:
            self.page.after_cancel(self.page._trim_after)
        self.page.destroy()
        self.root.update()

    # ------------------------------------------------------------------ helpers
    def settle(self, limit=30.0):
        end = time.time() + limit
        while time.time() < end:
            self.root.update()
            if not self.page._pending and self.page._build_after is None and self.page._trim_after is None:
                return
            time.sleep(0.002)
        self.fail("the list never finished building")

    def packed_keys(self):
        by_widget = {str(row): key for key, row in self.page.rows.items()}
        return [by_widget[str(w)] for w in self.page.list_body.pack_slaves() if str(w) in by_widget]

    def visible_keys(self):
        return [i.key for i in self.page._visible]

    def add(self, jobs):
        self.page.add_job_rows(jobs)

    # ------------------------------------------------------------------ rows
    def test_a_long_list_is_built_in_slices_and_ends_up_complete_and_in_order(self):
        jobs = make_jobs(120)
        self.add(jobs)
        self.assertLessEqual(len(self.page.rows), dp.FIRST_BATCH, "only the first batch is built before returning")
        self.assertTrue(self.page._pending, "the rest is still to come")
        self.settle()
        self.assertEqual(len(self.page.rows), 120)
        self.assertEqual(self.packed_keys(), self.visible_keys())

    def test_switching_tabs_hides_rows_instead_of_destroying_them(self):
        self.add(make_jobs(30))
        self.settle()
        before = dict(self.page.rows)
        self.page.set_category("failed")
        self.root.update()
        self.assertEqual([k for k in self.packed_keys()], [])
        self.assertEqual(len(self.page.rows), 30, "rows are kept while hidden")
        self.page.set_category("all")
        self.settle()
        self.assertEqual(self.packed_keys(), self.visible_keys())
        for key, row in before.items():
            self.assertIs(self.page.rows[key], row, "the very same row object comes back")

    def test_going_back_to_a_tab_is_fast(self):
        self.add(make_jobs(80))
        self.settle()
        self.page.set_category("failed")
        self.root.update()
        t = time.perf_counter()
        self.page.set_category("all")
        self.root.update()
        self.assertLess(time.perf_counter() - t, 0.4, "80 cached rows must not be rebuilt")

    def test_surplus_hidden_rows_are_destroyed_gradually_least_recently_shown_first(self):
        dp.MAX_HIDDEN_ROWS = 12
        jobs = make_jobs(40)
        self.add(jobs)
        self.settle()
        self.page.set_category("failed")             # everything is hidden now
        self.root.update()
        self.assertEqual(len(self.page.rows), 40, "nothing is destroyed inside the tab switch itself")
        self.settle()
        self.assertEqual(len(self.page.rows), 12)
        self.page.set_category("all")                # the missing ones are simply built again
        self.settle()
        self.assertEqual(len(self.page.rows), 40)
        self.assertEqual(self.packed_keys(), self.visible_keys())

    def test_an_item_that_is_gone_loses_its_row(self):
        jobs = make_jobs(10)
        self.add(jobs)
        self.settle()
        gone = self.page.jobs.pop(0)
        self.page._relayout()
        self.settle()
        self.assertNotIn(gone.key, self.page.rows)
        self.assertEqual(len(self.page.rows), 9)
        self.assertEqual(self.packed_keys(), self.visible_keys())

    def test_a_hidden_row_comes_back_unselected(self):
        self.add(make_jobs(6))
        self.settle()
        first = self.page._visible[0]
        self.page.click_row(self.page.rows[first.key])
        self.assertTrue(self.page.rows[first.key].selected)
        self.page.set_category("failed")
        self.root.update()
        self.page.set_category("all")
        self.settle()
        self.assertFalse(self.page.rows[first.key].selected)
        self.assertEqual(self.page.selected, [])

    def test_ordering_stays_right_through_random_status_changes_and_tab_switches(self):
        rng = random.Random(42)
        jobs = make_jobs(40)
        self.add(jobs)
        self.settle()
        for step in range(60):
            action = rng.choice(["status", "status", "tab", "search"])
            if action == "status":
                job = rng.choice(jobs)
                job.status = rng.choice(["queued", "downloading", "paused", "error", "queued"])
                self.page.update_job_row(job)
            elif action == "tab":
                self.page.set_category(rng.choice(["all", "downloading", "queued", "paused", "failed", "all"]))
            else:
                self.page.set_search(rng.choice(["", "number 1", "number 2", "title", ""]))
            self.settle()
            self.assertEqual(self.packed_keys(), self.visible_keys(), f"step {step}: {action}")
            for key in set(self.page.rows) - set(self.packed_keys()):
                self.assertEqual(self.page.rows[key].winfo_manager(), "", "hidden rows are not packed")

    def test_a_progress_tick_for_a_row_not_built_yet_does_not_restart_the_layout(self):
        jobs = make_jobs(60)
        self.add(jobs)
        self.assertTrue(self.page._pending, "a 60-row list is still being built at this point")
        pending_job = next(j for j in jobs if ("job", j.id) in self.page._pending or j.id in str(self.page._pending))
        calls = []
        original = self.page._relayout
        self.page._relayout = lambda: calls.append(1) or original()
        pending_job.progress = 40
        self.page.update_job_row(pending_job)
        self.assertEqual(calls, [], "its row arrives with the build; the layout must not start over")
        self.assertGreaterEqual(self.app.summary_updates, 1)
        self.page._relayout = original

    # ------------------------------------------------------------------ cheap updates
    def _row(self):
        self.add(make_jobs(3))
        self.settle()
        self.root.update()
        return self.page.rows[self.page._visible[0].key]

    def test_unchanged_text_is_not_measured_again(self):
        row = self._row()
        row.refresh_content()
        measured = []
        real = row._font_name.measure
        row._font_name.measure = lambda text: measured.append(text) or real(text)
        real_meta = row._font_meta.measure
        row._font_meta.measure = lambda text: measured.append(text) or real_meta(text)
        for _ in range(20):
            row.refresh_content()
        self.assertEqual(measured, [], "the same name/meta at the same width must not be measured again")

    def test_a_changed_name_is_measured_and_refitted(self):
        row = self._row()
        row.refresh_content()
        row.item.name_override = None
        row.item.job.title = "A completely different and rather long title " * 4
        row.refresh_content()
        self.assertNotEqual(row._name_key, None)

    def test_repainting_with_the_same_colour_does_nothing(self):
        row = self._row()
        row._paint()
        configured = []
        real = row.name_lbl.configure
        row.name_lbl.configure = lambda *a, **k: configured.append(k) or real(*a, **k)
        for _ in range(10):
            row._paint()
        self.assertEqual(configured, [])
        row.set_selected(True)                       # a real change does repaint
        self.assertTrue(configured)

    def test_action_buttons_are_only_rebuilt_when_the_status_changes(self):
        row = self._row()
        row.refresh_content()
        calls = []
        real = row.btn1.configure
        row.btn1.configure = lambda *a, **k: calls.append(1) or real(*a, **k)
        for _ in range(10):
            row.refresh_content()
        self.assertEqual(calls, [])
        row.item.job.status = "paused"
        row.refresh_content()
        self.assertTrue(calls, "a status change must rebuild the buttons")

    def test_a_progress_update_is_cheap(self):
        jobs = make_jobs(5)
        self.add(jobs)
        self.settle()
        job = jobs[0]
        job.status = "downloading"
        self.page.update_job_row(job)
        self.root.update()
        t = time.perf_counter()
        for k in range(100):
            job.progress = 5 + k * 0.5
            job.speed = "2.0 MiB/s"
            job.eta = "00:10"
            self.page.update_job_row(job)
        per_update_ms = (time.perf_counter() - t) * 10
        self.assertLess(per_update_ms, 3.0, f"{per_update_ms:.2f} ms per progress update (was ~7 ms)")


if __name__ == "__main__":
    unittest.main()
