"""Imported first by every test module: puts the repo root on sys.path so
`python -m unittest discover -s tests` can import core/, ui/ and version."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def destroy_tk(root):
    """Destroy a Tk root AND collect its garbage on this (the main) thread.

    A destroyed root can stay alive inside reference cycles; when the garbage collector
    later finalises it on some OTHER thread (a worker started by a different test), Tcl
    aborts the whole process with "Tcl_AsyncDelete: async handler deleted by the wrong
    thread". Collecting right here makes that impossible."""
    import gc
    try:
        root.destroy()
    except Exception:
        pass
    gc.collect()
