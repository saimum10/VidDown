"""
ui/threads.py
Run slow work on a background thread and hand the result back to Tk safely.

Tkinter is only safe on the thread that runs the main loop: a worker calling
widget.after() can raise "main thread is not in main loop" (or hang) when the
window is closing. So the worker only fills a small dict, and the Tk thread
polls it.
"""
import threading
import tkinter

POLL_MS = 120


def run_in_thread(widget, work, on_done, name="viddown-worker"):
    """work() runs on a thread; on_done(result, error) runs on the Tk thread.
    Exactly one of result/error is meaningful (error is the exception or None).
    If the widget is destroyed meanwhile, the result is dropped quietly."""
    box = {}

    def _target():
        try:
            box["ok"] = work()
        except BaseException as exc:          # reported to on_done, never lost
            box["err"] = exc
        finally:
            box["done"] = True

    threading.Thread(target=_target, daemon=True, name=name).start()

    def _poll():
        try:
            if box.get("done"):
                on_done(box.get("ok"), box.get("err"))
            else:
                widget.after(POLL_MS, _poll)
        except tkinter.TclError:              # window already gone
            pass

    try:
        widget.after(POLL_MS, _poll)
    except tkinter.TclError:
        pass
