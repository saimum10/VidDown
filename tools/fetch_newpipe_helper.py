#!/usr/bin/env python3
"""
tools/fetch_newpipe_helper.py
Imports the NATIVE NewPipe helper into bin/newpipe/ from the releases of
https://github.com/saimumhabib/viddown-native-bundle

That repo builds every tool on its own and releases it under the tool's own version:
the helper is release "newpipe-v<version>" with one zip per platform
(viddown-native-newpipe-<linux-x86_64|windows-x86_64>-<version>.zip). A rolling release
named "index" holds components.json, which says which tag is the newest.

What this script does:
  1. reads index/components.json (or, with --tag, goes straight to that release),
  2. downloads the zip of this platform and checks it against the release's SHA256SUMS.txt,
  3. checks every file inside the zip against the zip's own SHA256SUMS,
  4. installs ONLY an allow-list of files into bin/newpipe/:
        viddown-newpipe[.exe]   the helper (no Java needed)
        cacert.pem              its CA certificates
        native-helper-info/     BUILD_INFO.json, THIRD_PARTY_NOTICES.md, LICENSE, licenses/
        native-helper.json      which release this came from
     Nothing is changed unless every check passed.

The checksum comes from the same release as the zip: it catches a corrupted or cut-off
download and a tampered zip, but it is not protection against a compromised release.
The URLs read from the index must stay inside the repo's release area, and only https is used.

Standard library only (the Debian build container has just python3).

usage:
  python tools/fetch_newpipe_helper.py [--platform linux-x86_64|windows-x86_64]
         [--dest bin/newpipe] [--tag newpipe-v0.27.1] [--repo owner/name] [--refresh]

  --tag      pin a version instead of "newest in the index" (reproducible builds)
  --refresh  download again even if the helper is already in --dest
Environment: VIDDOWN_NATIVE_REPO overrides the repository.
Exit code 0 = installed, or already there. 1 = nothing installed (the reason is printed).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

DEFAULT_REPO = "saimumhabib/viddown-native-bundle"
PLATFORMS = ("linux-x86_64", "windows-x86_64")
INDEX_TAG = "index"
INFO_DIR = "native-helper-info"
STATE_FILE = "native-helper.json"

TAG_RX = re.compile(r"^newpipe-v([0-9]+(?:\.[0-9]+){2,3})$")
REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MAX_ZIP = 150 * 1024 * 1024            # the real zip is a few tens of MB
MAX_INDEX = 1024 * 1024
MAX_UNPACKED = 400 * 1024 * 1024
ATTEMPTS = 3
TIMEOUT = 30


class FetchError(Exception):
    pass


def log(msg):
    print(f"[newpipe-helper] {msg}", flush=True)


def exe_name(platform):
    return "viddown-newpipe.exe" if platform.startswith("windows") else "viddown-newpipe"


def detect_platform():
    if sys.platform.startswith("win"):
        return "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    raise FetchError(f"no native NewPipe helper is published for {sys.platform} "
                     f"(platforms: {', '.join(PLATFORMS)})")


# ------------------------------------------------------------------ network --
class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    allow_http = False

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not self.allow_http and not newurl.lower().startswith("https://"):
            raise FetchError(f"refusing a redirect to a non-https address: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener(allow_http):
    handler = _HttpsOnlyRedirects()
    handler.allow_http = allow_http
    return urllib.request.build_opener(handler)


def download(url, limit, allow_http=False, dest=None):
    """GET `url` with retries. Returns the bytes, or writes to `dest` (a Path) and returns None."""
    if not allow_http and not url.lower().startswith("https://"):
        raise FetchError(f"refusing a non-https address: {url}")
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "VidDown-newpipe-helper-fetch"})
            with _opener(allow_http).open(req, timeout=TIMEOUT) as resp:
                size, chunks = 0, []
                out = open(dest, "wb") if dest else None
                try:
                    while True:
                        chunk = resp.read(1 << 16)
                        if not chunk:
                            break
                        size += len(chunk)
                        if size > limit:
                            raise FetchError(f"{url} is larger than the allowed {limit} bytes")
                        if out:
                            out.write(chunk)
                        else:
                            chunks.append(chunk)
                finally:
                    if out:
                        out.close()
                if size == 0:
                    raise FetchError(f"{url} was empty")
                return None if dest else b"".join(chunks)
        except FetchError:
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code in (401, 403, 404):
                break                                   # will not get better by waiting
            if attempt < ATTEMPTS:
                time.sleep(attempt)
    raise FetchError(f"could not download {url}: {last}")


# ------------------------------------------------------------------ resolving --
def release_prefix(repo):
    return f"https://github.com/{repo}/releases/download/"


def asset_name(platform, version):
    return f"viddown-native-newpipe-{platform}-{version}.zip"


def resolve(repo, platform, tag=None, index_url=None, prefix=None, allow_http=False):
    """-> (tag, version, base_url, asset). Reads the index unless a tag is given."""
    prefix = prefix or release_prefix(repo)
    if tag:
        m = TAG_RX.match(tag)
        if not m:
            raise FetchError(f"'{tag}' is not a NewPipe helper tag (expected newpipe-v<version>)")
        version = m.group(1)
        return tag, version, f"{prefix}{tag}/", asset_name(platform, version)

    url = index_url or f"{prefix}{INDEX_TAG}/components.json"
    log(f"reading {url}")
    try:
        index = json.loads(download(url, MAX_INDEX, allow_http).decode("utf-8"))
    except ValueError as exc:
        raise FetchError(f"components.json is not valid JSON: {exc}")
    if not isinstance(index, dict) or index.get("schema") != 1:
        raise FetchError(f"components.json has schema {index.get('schema') if isinstance(index, dict) else '?'}; "
                         "this script understands schema 1 - update the script")
    entry = (index.get("tools") or {}).get("newpipe")
    if not isinstance(entry, dict):
        raise FetchError("components.json lists no 'newpipe' tool: no helper has been released yet "
                         "(its CI build has to pass once)")
    tag, version, base = entry.get("tag", ""), entry.get("version", ""), entry.get("base_url", "")
    m = TAG_RX.match(tag)
    if not m or m.group(1) != version:
        raise FetchError(f"index entry is inconsistent: tag '{tag}', version '{version}'")
    if platform not in (entry.get("platforms") or []):
        raise FetchError(f"the newest helper ({tag}) has no build for {platform} "
                         f"(it has: {', '.join(entry.get('platforms') or []) or 'none'})")
    # GitHub treats owner/repo case-insensitively, and an index written before the repo was renamed to
    # lowercase can still spell it with other letter case - so compare without case, and then use OUR
    # address (DEFAULT_REPO, lowercase), not the index's spelling.
    expected_base = f"{prefix}{tag}/"
    if base.lower() != expected_base.lower():
        raise FetchError(f"index base_url '{base}' is outside the repo's release area; refusing it")
    template = entry.get("asset", "")
    asset = template.replace("{platform}", platform)
    if asset != asset_name(platform, version):
        raise FetchError(f"index asset name '{asset}' is not the expected '{asset_name(platform, version)}'")
    return tag, version, expected_base, asset


# ------------------------------------------------------------------ verifying --
def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_sums(text):
    """'<64 hex>  <name>' lines (sha256sum style, optional '*' before the name) -> {name: hex}."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^([0-9a-fA-F]{64}) [ *](.+)$", line.strip())
        if m:
            out[m.group(2)] = m.group(1).lower()
    return out


def wanted(name, platform):
    """Where a zip member goes inside --dest (relative, posix), or None to leave it out."""
    exe = exe_name(platform)
    if name == f"bin/newpipe/{exe}":
        return exe
    if name == "bin/newpipe/cacert.pem":
        return "cacert.pem"
    if name in ("BUILD_INFO.json", "THIRD_PARTY_NOTICES.md", "LICENSE"):
        return f"{INFO_DIR}/{name}"
    if name.startswith("licenses/") and not name.endswith("/"):
        return f"{INFO_DIR}/{name}"
    return None


def safe_relative(rel):
    p = PurePosixPath(rel)
    return not p.is_absolute() and ".." not in p.parts and rel == str(p) and "\\" not in rel


def extract(zip_path, platform, staging):
    """Check every member against the zip's SHA256SUMS and write the allow-listed ones to `staging`."""
    exe = exe_name(platform)
    with zipfile.ZipFile(zip_path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        if "SHA256SUMS" not in names:
            raise FetchError("the zip has no SHA256SUMS")
        if sum(i.file_size for i in z.infolist()) > MAX_UNPACKED:
            raise FetchError("the zip unpacks to an unreasonable size")
        sums = parse_sums(z.read("SHA256SUMS").decode("utf-8", "replace"))
        for member in names:
            if member == "SHA256SUMS":
                continue
            if member not in sums:
                raise FetchError(f"'{member}' is in the zip but not in its SHA256SUMS")
            if hashlib.sha256(z.read(member)).hexdigest() != sums[member]:
                raise FetchError(f"'{member}' does not match the zip's SHA256SUMS - the zip was altered")
        installed = []
        for member in names:
            rel = wanted(member, platform)
            if rel is None:
                continue
            if not safe_relative(rel):
                raise FetchError(f"unsafe path in the zip: {member}")
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))
            installed.append(rel)
    if exe not in installed:
        raise FetchError(f"the zip does not contain bin/newpipe/{exe}")
    if "cacert.pem" not in installed:
        raise FetchError("the zip does not contain bin/newpipe/cacert.pem")
    os.chmod(staging / exe, 0o755)
    return installed


def install(staging, dest, platform):
    """Move the checked files into `dest`, replacing the previous helper."""
    dest.mkdir(parents=True, exist_ok=True)
    for old in (exe_name(platform), "cacert.pem", STATE_FILE):
        if (dest / old).exists():
            (dest / old).unlink()
    if (dest / INFO_DIR).exists():
        shutil.rmtree(dest / INFO_DIR)
    for item in sorted(staging.iterdir()):
        shutil.move(str(item), str(dest / item.name))


# ------------------------------------------------------------------ main flow --
def fetch_helper(dest, platform, repo=DEFAULT_REPO, tag=None, refresh=False, index_url=None, prefix=None,
                 allow_http=False):
    dest = Path(dest)
    exe = exe_name(platform)
    if (dest / exe).is_file() and not refresh:
        log(f"{dest / exe} is already there - not downloading (use --refresh to replace it)")
        return 0
    if not REPO_RX.match(repo):
        raise FetchError(f"'{repo}' is not an owner/name repository")

    tag, version, base, asset = resolve(repo, platform, tag, index_url, prefix, allow_http)
    log(f"helper release {tag} -> {asset}")
    with tempfile.TemporaryDirectory(prefix="viddown-newpipe-") as tmp:
        tmp = Path(tmp)
        sums = parse_sums(download(base + "SHA256SUMS.txt", MAX_INDEX, allow_http).decode("utf-8", "replace"))
        if asset not in sums:
            raise FetchError(f"{asset} is not listed in the release's SHA256SUMS.txt")
        zip_path = tmp / asset
        download(base + asset, MAX_ZIP, allow_http, dest=zip_path)
        actual = sha256_file(zip_path)
        if actual != sums[asset]:
            raise FetchError(f"{asset}: SHA-256 is {actual}, the release says {sums[asset]} - download rejected")
        log(f"SHA-256 of the zip matches the release ({actual[:16]}...)")
        staging = tmp / "staging"
        staging.mkdir()
        installed = extract(zip_path, platform, staging)
        (staging / STATE_FILE).write_text(json.dumps({
            "source_repo": repo, "tag": tag, "version": version, "asset": asset, "platform": platform,
            "zip_sha256": actual, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }, indent=2) + "\n")
        install(staging, dest, platform)
    log(f"installed into {dest}: {', '.join(sorted(i for i in installed if '/' not in i))} "
        f"+ {sum(1 for i in installed if i.startswith(INFO_DIR + '/'))} licence/notice files")
    return 0


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--platform", choices=PLATFORMS)
    ap.add_argument("--dest", default=str(root / "bin" / "newpipe"))
    ap.add_argument("--tag", help="pin a release, e.g. newpipe-v0.27.1 (default: the newest in the index)")
    ap.add_argument("--repo", default=os.environ.get("VIDDOWN_NATIVE_REPO", DEFAULT_REPO))
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args(argv)
    try:
        return fetch_helper(a.dest, a.platform or detect_platform(), a.repo, a.tag, a.refresh)
    except FetchError as exc:
        print(f"[newpipe-helper] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
