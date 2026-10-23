"""Imported first by every test module: puts the repo root on sys.path so
`python -m unittest discover -s tests` can import core/, ui/ and version."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
