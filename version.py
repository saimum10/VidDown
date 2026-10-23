"""
version.py
Single source of truth for the app version. Everything else (the header
in ui/main_window.py, the About page, build_deb.sh's packaging metadata)
reads from here instead of hard-coding its own copy of the version string
— that's how v1.0/v2.0 drifted out of sync with each other before.

Bump this one line when releasing a new version; nothing else needs to change.
"""
__version__ = "2.9.0.1"
