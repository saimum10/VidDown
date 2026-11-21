"""The yt-dlp watcher: when it builds, when it does NOT, and that the workflows are wired up."""
import _setup  # noqa: F401  (must stay first)

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

R = _setup.ROOT
SCRIPT = os.path.join(R, ".github", "scripts", "ytdlp_watch.py")

spec = importlib.util.spec_from_file_location("ytdlp_watch", SCRIPT)
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

try:
    import yaml
except ImportError:                       # PyYAML only needed for the workflow-structure tests
    yaml = None


class DecideTests(unittest.TestCase):
    def test_no_new_release_means_no_build(self):
        self.assertEqual(W.decide("2026.10.21", "2026.10.21"), "none")

    def test_older_release_on_pypi_never_builds(self):          # e.g. a yanked/rolled-back release
        self.assertEqual(W.decide("2026.10.9", "2026.10.21"), "none")

    def test_newer_release_builds(self):
        self.assertEqual(W.decide("2026.11.2", "2026.10.21"), "build")

    def test_comparison_is_numeric_not_textual(self):
        self.assertEqual(W.decide("2026.10.21", "2026.10.9"), "build")
        self.assertEqual(W.decide("2026.10.9", "2026.10.21"), "none")

    def test_first_run_only_records_a_baseline(self):
        self.assertEqual(W.decide("2026.10.21", None), "baseline")
        self.assertEqual(W.decide("2026.10.21", ""), "baseline")


class NextVersionTests(unittest.TestCase):
    def test_unreleased_current_version_is_used_as_it_is(self):
        self.assertEqual(W.next_vidown_version("2.9.0.5", ["v2.9.0.4", "v2.9.0.2"]), "2.9.0.5")

    def test_released_version_is_bumped(self):
        self.assertEqual(W.next_vidown_version("2.9.0.5", ["v2.9.0.5", "v2.9.0.4"]), "2.9.0.6")

    def test_taken_tags_are_skipped(self):
        self.assertEqual(W.next_vidown_version("2.9.0.5", ["v2.9.0.5", "v2.9.0.6", "v2.9.0.7"]), "2.9.0.8")

    def test_three_part_version_gets_a_fourth_number(self):
        self.assertEqual(W.next_vidown_version("2.9.0", ["v2.9.0"]), "2.9.0.1")


class FilesTests(unittest.TestCase):
    def _repo(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "browser_extension"))
        os.makedirs(os.path.join(tmp, ".github"))
        shutil.copy(os.path.join(R, "version.py"), tmp)
        shutil.copy(os.path.join(R, "browser_extension", "manifest.json"), os.path.join(tmp, "browser_extension"))
        return tmp

    def test_write_version_changes_version_py_and_manifest_only_in_the_version_field(self):
        tmp = self._repo()
        before = open(os.path.join(tmp, "browser_extension", "manifest.json"), encoding="utf-8").read()
        W.write_version("9.8.7.6", os.path.join(tmp, "version.py"), os.path.join(tmp, "browser_extension", "manifest.json"))
        self.assertEqual(W.current_vidown_version(open(os.path.join(tmp, "version.py"), encoding="utf-8").read()), "9.8.7.6")
        m = json.load(open(os.path.join(tmp, "browser_extension", "manifest.json"), encoding="utf-8"))
        self.assertEqual(m["version"], "9.8.7.6")
        after = open(os.path.join(tmp, "browser_extension", "manifest.json"), encoding="utf-8").read()
        self.assertEqual(re.sub(r'"version"\s*:\s*"[^"]+"', "", before, count=1),
                         re.sub(r'"version"\s*:\s*"[^"]+"', "", after, count=1))

    def test_app_and_extension_versions_are_in_step(self):
        v = W.current_vidown_version(open(os.path.join(R, "version.py"), encoding="utf-8").read())
        m = json.load(open(os.path.join(R, "browser_extension", "manifest.json"), encoding="utf-8"))
        self.assertEqual(m["version"], v)

    def _run(self, tmp, latest, baseline, tags):
        """Run the real script inside a throw-away git repo."""
        if baseline is not None:
            with open(os.path.join(tmp, ".github", "ytdlp-version.txt"), "w") as f:
                f.write(baseline + "\n")
        script_dir = os.path.join(tmp, ".github", "scripts")
        os.makedirs(script_dir, exist_ok=True)
        shutil.copy(SCRIPT, script_dir)
        git = lambda *a: subprocess.run(["git", *a], cwd=tmp, capture_output=True, text=True, check=True)
        git("init", "-q")
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x")
        for t in tags:
            git("tag", t)
        out = os.path.join(tmp, "gh_output.txt")
        env = {**os.environ, "YTDLP_LATEST": latest, "GITHUB_OUTPUT": out}
        r = subprocess.run([sys.executable, os.path.join(script_dir, "ytdlp_watch.py")], cwd=tmp, env=env,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return dict(line.split("=", 1) for line in open(out).read().split())

    def test_script_end_to_end_new_release(self):
        tmp = self._repo()
        current = W.current_vidown_version(open(os.path.join(tmp, "version.py"), encoding="utf-8").read())
        res = self._run(tmp, "2099.01.02", "2099.01.01", [f"v{current}"])
        self.assertEqual(res["action"], "build")
        self.assertEqual(res["ytdlp"], "2099.01.02")
        self.assertNotEqual(res["vidown"], current)
        self.assertEqual(open(os.path.join(tmp, ".github", "ytdlp-version.txt")).read().strip(), "2099.01.02")
        self.assertEqual(W.current_vidown_version(open(os.path.join(tmp, "version.py"), encoding="utf-8").read()), res["vidown"])

    def test_script_end_to_end_nothing_new_changes_nothing(self):
        tmp = self._repo()
        vp = open(os.path.join(tmp, "version.py"), encoding="utf-8").read()
        res = self._run(tmp, "2099.01.01", "2099.01.01", [])
        self.assertEqual(res["action"], "none")
        self.assertEqual(open(os.path.join(tmp, "version.py"), encoding="utf-8").read(), vp)

    def test_script_end_to_end_first_run_records_baseline_without_building(self):
        tmp = self._repo()
        vp = open(os.path.join(tmp, "version.py"), encoding="utf-8").read()
        res = self._run(tmp, "2099.01.01", None, [])
        self.assertEqual(res["action"], "baseline")
        self.assertEqual(open(os.path.join(tmp, ".github", "ytdlp-version.txt")).read().strip(), "2099.01.01")
        self.assertEqual(open(os.path.join(tmp, "version.py"), encoding="utf-8").read(), vp)   # version untouched


@unittest.skipIf(yaml is None, "PyYAML not installed")
class WorkflowWiringTests(unittest.TestCase):
    def _load(self, name):
        with open(os.path.join(R, ".github", "workflows", name), encoding="utf-8") as f:
            d = yaml.safe_load(f)
        d["_on"] = d.get("on", d.get(True))          # PyYAML reads the key `on` as True
        return d

    def test_ordinary_pushes_do_not_build(self):
        on = self._load("build.yml")["_on"]
        push = on.get("push") or {}
        self.assertNotIn("branches", push, "a push to main/master must not start a build")
        self.assertEqual(push.get("tags"), ["v*"])
        self.assertIn("workflow_dispatch", on)
        self.assertNotIn("schedule", on)
        self.assertNotIn("pull_request", on)

    def test_build_accepts_the_yt_dlp_version_input(self):
        inp = self._load("build.yml")["_on"]["workflow_dispatch"]["inputs"]["ytdlp_version"]
        self.assertFalse(inp.get("required"))

    def test_watch_runs_on_a_schedule_and_by_hand_only(self):
        on = self._load("ytdlp-watch.yml")["_on"]
        self.assertEqual(set(on), {"schedule", "workflow_dispatch"})
        self.assertTrue(re.fullmatch(r"[\d*/, -]+( [\d*/, -]+){4}", on["schedule"][0]["cron"]))

    def test_watch_has_the_permissions_it_needs(self):
        perms = self._load("ytdlp-watch.yml")["permissions"]
        self.assertEqual(perms, {"contents": "write", "actions": "write"})

    def test_watch_only_starts_a_build_when_the_script_said_build(self):
        steps = self._load("ytdlp-watch.yml")["jobs"]["watch"]["steps"]
        start = [s for s in steps if "gh workflow run" in (s.get("run") or "")][0]
        self.assertIn("== 'build'", start["if"])
        self.assertIn("build.yml", start["run"])
        self.assertIn("ytdlp_version=", start["run"])
        commit = [s for s in steps if "git commit" in (s.get("run") or "")][0]
        self.assertIn("!= 'none'", commit["if"])

    def test_every_install_path_is_pinned_when_a_version_is_given(self):
        jobs = self._load("build.yml")["jobs"]
        for job, step_name in (("test", "Install dependencies"), ("build-deb", "Build .deb"),
                               ("build-windows", "Build installer + portable zip")):
            step = [s for s in jobs[job]["steps"] if s.get("name") == step_name][0]
            self.assertIn("ytdlp_version", step["env"]["YTDLP_VERSION"], job)
            self.assertIn("PIP_CONSTRAINT", step["run"], job)
            self.assertIn("yt-dlp==", step["run"], job)

    def test_the_tag_comes_from_version_py_which_the_watcher_bumps(self):
        with open(os.path.join(R, ".github", "workflows", "build.yml"), encoding="utf-8") as f:
            self.assertIn("version.py", f.read())


if __name__ == "__main__":
    unittest.main()
