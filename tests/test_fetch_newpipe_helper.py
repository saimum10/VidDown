"""Tests for tools/fetch_newpipe_helper.py (a local HTTP server stands in for GitHub releases)."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("fetch_newpipe_helper", ROOT / "tools" / "fetch_newpipe_helper.py")
F = importlib.util.module_from_spec(spec)
spec.loader.exec_module(F)

PLATFORM = "linux-x86_64"
VERSION = "0.27.1"
TAG = f"newpipe-v{VERSION}"
ASSET = f"viddown-native-newpipe-{PLATFORM}-{VERSION}.zip"


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_zip(extra=None, tamper=None, drop=(), platform=PLATFORM):
    """A zip laid out like scripts/package_newpipe.py of the native bundle repo makes it."""
    exe = F.exe_name(platform)
    files = {
        f"bin/newpipe/{exe}": b"\x7fELF fake helper",
        "bin/newpipe/cacert.pem": b"-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n",
        "BUILD_INFO.json": b'{"kind": "newpipe-helper"}',
        "THIRD_PARTY_NOTICES.md": b"# notices",
        "LICENSE": b"GPL text",
        "licenses/newpipe-extractor/LICENSE": b"extractor licence",
        "licenses/jars/jsoup-1.0/LICENSE": b"MIT",
    }
    files.update(extra or {})
    for d in drop:
        files.pop(d)
    sums = "".join(f"{sha(b)}  {n}\n" for n, b in sorted(files.items())).encode()
    if tamper:
        files[tamper] = files[tamper] + b" tampered"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SHA256SUMS", sums)
        for n, b in files.items():
            z.writestr(n, b)
    return buf.getvalue()


class Server:
    def __init__(self, routes):
        outer = self
        self.routes, self.hits = routes, []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits.append(self.path)
                body = outer.routes.get(self.path)
                if body is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def index_json(**over):
    entry = {"version": VERSION, "tag": TAG, "content_id": "0123456789ab",
             "base_url": None, "asset": f"viddown-native-newpipe-{{platform}}-{VERSION}.zip",
             "platforms": ["linux-x86_64", "windows-x86_64"]}
    entry.update(over)
    return entry


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.dest = self.tmp / "bin" / "newpipe"
        self.servers = []
        F.ATTEMPTS = 1

    def tearDown(self):
        for s in self.servers:
            s.close()

    def serve(self, zip_bytes=None, sums=None, index=None, index_over=None):
        zip_bytes = zip_bytes if zip_bytes is not None else make_zip()
        routes = {f"/{TAG}/{ASSET}": zip_bytes,
                  f"/{TAG}/SHA256SUMS.txt": (sums if sums is not None else f"{sha(zip_bytes)}  {ASSET}\n").encode()}
        srv = Server(routes)
        self.servers.append(srv)
        over = {"base_url": f"{srv.url}{TAG}/"}
        over.update(index_over or {})
        entry = index_json(**over)
        srv.routes["/index/components.json"] = json.dumps(
            index if index is not None else {"schema": 1, "tools": {"newpipe": entry}}).encode()
        return srv

    def run_fetch(self, srv, **kw):
        kw.setdefault("platform", PLATFORM)
        return F.fetch_helper(self.dest, repo="o/r", index_url=srv.url + "index/components.json",
                              prefix=srv.url, allow_http=True, **kw)

    def test_installs_from_the_index(self):
        srv = self.serve()
        self.assertEqual(self.run_fetch(srv), 0)
        exe = self.dest / "viddown-newpipe"
        self.assertEqual(exe.read_bytes(), b"\x7fELF fake helper")
        self.assertTrue(os.access(exe, os.X_OK))
        self.assertTrue((self.dest / "cacert.pem").is_file())
        info = self.dest / F.INFO_DIR
        for f in ("BUILD_INFO.json", "THIRD_PARTY_NOTICES.md", "LICENSE", "licenses/newpipe-extractor/LICENSE",
                  "licenses/jars/jsoup-1.0/LICENSE"):
            self.assertTrue((info / f).is_file(), f)
        state = json.loads((self.dest / F.STATE_FILE).read_text())
        self.assertEqual((state["tag"], state["version"], state["platform"]), (TAG, VERSION, PLATFORM))
        self.assertEqual(state["zip_sha256"], sha(make_zip()))

    def test_a_pinned_tag_skips_the_index(self):
        srv = self.serve()
        self.run_fetch(srv, tag=TAG)
        self.assertNotIn("/index/components.json", srv.hits)
        self.assertTrue((self.dest / "viddown-newpipe").is_file())

    def test_windows_gets_the_exe(self):
        z = make_zip(platform="windows-x86_64")
        asset = f"viddown-native-newpipe-windows-x86_64-{VERSION}.zip"
        srv = Server({f"/{TAG}/{asset}": z, f"/{TAG}/SHA256SUMS.txt": f"{sha(z)}  {asset}\n".encode()})
        self.servers.append(srv)
        srv.routes["/index/components.json"] = json.dumps({"schema": 1, "tools": {"newpipe": index_json(base_url=f"{srv.url}{TAG}/")}}).encode()
        self.run_fetch(srv, platform="windows-x86_64")
        self.assertTrue((self.dest / "viddown-newpipe.exe").is_file())

    def test_already_installed_is_left_alone_without_network(self):
        self.dest.mkdir(parents=True)
        (self.dest / "viddown-newpipe").write_bytes(b"mine")
        srv = self.serve()
        self.assertEqual(self.run_fetch(srv), 0)
        self.assertEqual(srv.hits, [])
        self.assertEqual((self.dest / "viddown-newpipe").read_bytes(), b"mine")
        self.run_fetch(srv, refresh=True)
        self.assertEqual((self.dest / "viddown-newpipe").read_bytes(), b"\x7fELF fake helper")

    def test_wrong_release_checksum_installs_nothing_and_keeps_the_old_helper(self):
        self.dest.mkdir(parents=True)
        (self.dest / "viddown-newpipe").write_bytes(b"old")
        srv = self.serve(sums=f"{'0' * 64}  {ASSET}\n")
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv, refresh=True)
        self.assertIn("download rejected", str(cm.exception))
        self.assertEqual((self.dest / "viddown-newpipe").read_bytes(), b"old")

    def test_asset_missing_from_sums_is_refused(self):
        srv = self.serve(sums=f"{'0' * 64}  something-else.zip\n")
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("not listed", str(cm.exception))

    def test_a_member_altered_inside_the_zip_is_refused(self):
        srv = self.serve(zip_bytes=make_zip(tamper="bin/newpipe/viddown-newpipe"))
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("altered", str(cm.exception))
        self.assertFalse(self.dest.exists() and any(self.dest.iterdir()))

    def test_an_unlisted_extra_member_is_refused(self):
        z = io.BytesIO(make_zip())
        with zipfile.ZipFile(z, "a") as zz:
            zz.writestr("bin/newpipe/surprise.exe", b"x")
        srv = self.serve(zip_bytes=z.getvalue())
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("not in its SHA256SUMS", str(cm.exception))

    def test_members_outside_the_allow_list_are_not_installed(self):
        srv = self.serve(zip_bytes=make_zip(extra={"bin/newpipe/other-tool": b"x", "../evil.txt": b"x", "docs/x.md": b"x"}))
        self.run_fetch(srv)
        self.assertFalse((self.dest / "other-tool").exists())
        self.assertFalse((self.tmp / "evil.txt").exists())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), sorted(["viddown-newpipe", "cacert.pem", F.INFO_DIR, F.STATE_FILE]))

    def test_zip_without_the_helper_or_the_ca_bundle_is_refused(self):
        for missing in ("bin/newpipe/viddown-newpipe", "bin/newpipe/cacert.pem"):
            with self.subTest(missing=missing):
                srv = self.serve(zip_bytes=make_zip(drop=(missing,)))
                with self.assertRaises(F.FetchError):
                    self.run_fetch(srv)

    def test_index_pointing_outside_the_repo_is_refused(self):
        srv = self.serve(index_over={"base_url": "https://evil.example/download/newpipe-v0.27.1/"})
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("outside the repo", str(cm.exception))

    def test_index_may_spell_the_repo_with_other_letter_case(self):
        # an index published before the repo name became lowercase spells the address with other letter case
        srv = self.serve()
        entry = json.loads(srv.routes["/index/components.json"])
        entry["tools"]["newpipe"]["base_url"] = (srv.url + TAG + "/").replace("http://", "HTTP://").upper().replace("NEWPIPE-V", "newpipe-v")
        srv.routes["/index/components.json"] = json.dumps(entry).encode()
        self.run_fetch(srv)
        self.assertTrue((self.dest / "viddown-newpipe").is_file())
        self.assertNotIn("HTTP://", "".join(srv.hits))          # downloaded from our address, not the index's spelling

    def test_odd_asset_names_and_tags_are_refused(self):
        for over in ({"asset": "evil.exe"}, {"asset": "../x-{platform}.zip"}, {"tag": "newpipe-v9.9.9"}, {"tag": "yt-dlp-v1.0"}):
            with self.subTest(over=over), self.assertRaises(F.FetchError):
                self.run_fetch(self.serve(index_over=over))
        with self.assertRaises(F.FetchError):
            F.resolve("o/r", PLATFORM, tag="newpipe-v1.0; rm -rf /")

    def test_platform_missing_in_the_index(self):
        srv = self.serve(index_over={"platforms": ["windows-x86_64"]})
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("no build for linux-x86_64", str(cm.exception))

    def test_unknown_schema_and_missing_tool(self):
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(self.serve(index={"schema": 2, "tools": {}}))
        self.assertIn("schema", str(cm.exception))
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(self.serve(index={"schema": 1, "tools": {"yt-dlp": {}}}))
        self.assertIn("no helper has been released", str(cm.exception))

    def test_missing_index_is_a_clear_error(self):
        srv = self.serve()
        del srv.routes["/index/components.json"]
        with self.assertRaises(F.FetchError) as cm:
            self.run_fetch(srv)
        self.assertIn("could not download", str(cm.exception))

    def test_https_only_in_real_use(self):
        with self.assertRaises(F.FetchError):
            F.download("http://example.com/x", 100)
        with self.assertRaises(F.FetchError):
            F.fetch_helper(self.dest, PLATFORM, repo="not a repo")

    def test_parse_sums(self):
        h = "a" * 64
        self.assertEqual(F.parse_sums(f"{h}  a.zip\n{h} *b.zip\njunk\n"), {"a.zip": h, "b.zip": h})

    def test_default_repo(self):
        self.assertEqual(F.DEFAULT_REPO, "saimumhabib/viddown-native-bundle")
        self.assertEqual(F.release_prefix(F.DEFAULT_REPO), "https://github.com/saimumhabib/viddown-native-bundle/releases/download/")


if __name__ == "__main__":
    unittest.main()
