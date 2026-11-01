#!/usr/bin/env python3
"""Decide whether VidDown needs a new build because yt-dlp published a new release.

Run by .github/workflows/ytdlp-watch.yml (from the repository root):

    python .github/scripts/ytdlp_watch.py

What it does
  1. asks PyPI for the latest STABLE yt-dlp release,
  2. compares it with .github/ytdlp-version.txt (the yt-dlp version of the last build),
  3. if it is newer: writes the new baseline and bumps the VidDown version in
     version.py and browser_extension/manifest.json,
  4. prints  action=none|baseline|build , ytdlp=<version> , vidown=<version>  to
     $GITHUB_OUTPUT so the workflow knows what to commit and whether to start a build.

  action=none      nothing new           -> the workflow ends, NO build
  action=baseline  first run, no baseline -> the current yt-dlp version is only RECORDED
                                             (no build: nothing has changed yet)
  action=build     a newer yt-dlp exists -> files were changed; the workflow commits them
                                             and starts the build

The pure functions below are covered by tests/test_ytdlp_watch.py.
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE = os.path.join(ROOT, ".github", "ytdlp-version.txt")
VERSION_PY = os.path.join(ROOT, "version.py")
MANIFEST = os.path.join(ROOT, "browser_extension", "manifest.json")
PYPI_URL = "https://pypi.org/pypi/yt-dlp/json"


def version_key(text):
    """'2026.10.9' < '2026.10.21'; numeric, so it also orders 4-part versions."""
    return tuple(int(n) for n in re.findall(r"\d+", str(text or "")))


def decide(latest, baseline):
    """'baseline' | 'build' | 'none'."""
    if not baseline:
        return "baseline"
    return "build" if version_key(latest) > version_key(baseline) else "none"


def next_vidown_version(current, existing_tags):
    """Version for the new build. A version that was never released (no v<version> tag
    yet) is used as it is; otherwise the last number is raised until the tag is free."""
    tags = set(existing_tags)
    if f"v{current}" not in tags:
        return current
    parts = [int(p) for p in current.split(".")]
    while len(parts) < 4:
        parts.append(0)
    while True:
        parts[3] += 1
        candidate = ".".join(str(p) for p in parts)
        if f"v{candidate}" not in tags:
            return candidate


def read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def current_vidown_version(version_py_text):
    m = re.search(r'__version__\s*=\s*"([^"]+)"', version_py_text)
    if not m:
        raise SystemExit("version.py: no __version__ found")
    return m.group(1)


def write_version(new, version_py=VERSION_PY, manifest=MANIFEST):
    """Put `new` into version.py and the extension manifest (they carry the same number)."""
    text = read_text(version_py)
    out, n = re.subn(r'(__version__\s*=\s*")[^"]+(")', rf"\g<1>{new}\g<2>", text, count=1)
    if n != 1:
        raise SystemExit("version.py: could not rewrite __version__")
    with open(version_py, "w", encoding="utf-8") as f:
        f.write(out)
    mtext = read_text(manifest)
    if mtext is not None:
        out, n = re.subn(r'("version"\s*:\s*")[^"]+(")', rf"\g<1>{new}\g<2>", mtext, count=1)
        if n != 1:
            raise SystemExit("manifest.json: could not rewrite version")
        with open(manifest, "w", encoding="utf-8") as f:
            f.write(out)


def latest_stable_ytdlp(retries=4):
    """Latest non-pre-release version PyPI lists (PyPI's `info.version`)."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "viddown-ytdlp-watch"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)["info"]["version"]
        except Exception as exc:           # network hiccup / PyPI 5xx: wait and retry
            last = exc
            time.sleep(3 * (attempt + 1))
    raise SystemExit(f"could not read the latest yt-dlp version from PyPI: {last}")


def git_tags():
    out = subprocess.run(["git", "tag", "--list", "v*"], cwd=ROOT, capture_output=True, text=True)
    return out.stdout.split()


def emit(**values):
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(lines)
    print(lines, end="")


def main():
    latest = os.environ.get("YTDLP_LATEST") or latest_stable_ytdlp()   # env override: manual tests only
    baseline = (read_text(BASELINE) or "").strip() or None
    action = decide(latest, baseline)
    print(f"latest yt-dlp on PyPI: {latest} | last built with: {baseline or '(none recorded)'} -> {action}")

    vidown = current_vidown_version(read_text(VERSION_PY))
    if action in ("baseline", "build"):
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            f.write(latest + "\n")
    if action == "build":
        vidown = next_vidown_version(vidown, git_tags())
        write_version(vidown)
    emit(action=action, ytdlp=latest, vidown=vidown)


if __name__ == "__main__":
    sys.exit(main())
