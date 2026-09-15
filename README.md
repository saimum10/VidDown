# VidDown

Android video downloader (YouTube, TikTok, Instagram, Facebook, X) built with
Kotlin + Jetpack Compose, powered by yt-dlp running on-device via
[youtubedl-android](https://github.com/yausername/youtubedl-android).

## Stack

- **UI:** Kotlin, Jetpack Compose, Material 3, single-activity + Navigation-Compose
- **Engine:** `io.github.junkfood02.youtubedl-android` -- bundles a real yt-dlp
  binary + a Python runtime as a prebuilt, size-optimized AAR per ABI.
  `YtDlpEngine.kt` is a thin wrapper around it. ffmpeg (muxing/MP3 extraction)
  and aria2c (multi-connection speed boost) are included as the library's own
  separate `ffmpeg`/`aria2c` artifacts.
- **Downloads:** WorkManager (`DownloadWorker`) + a foreground notification;
  cancellation kills the real underlying yt-dlp process by id
  (`YoutubeDL.destroyProcessById`)
- **Cookies:** `CookieBridge.kt` silently reuses whatever the in-app browser
  (WebView) is logged into for extraction/download -- no separate "cookie"
  toggle anywhere, same as most similar apps
- **Storage:** Room (`downloads` table) -- one row per queued/running/finished item
- **Build:** Gradle, ABI-split APKs (arm64-v8a, armeabi-v7a, x86_64) + universal,
  built in GitHub Actions (`.github/workflows/build.yml`)

## Yes — GitHub Actions builds the whole thing by itself

Push this to a GitHub repo (or just enable Actions on it) and run the
`Build VidDown APKs` workflow. Every step below happens with **zero manual
input**:

1. Checks out the code, sets up JDK 17 + Android SDK + Gradle
2. `gradle assembleDebug` pulls yt-dlp/ffmpeg/aria2c automatically as normal
   Gradle dependencies (Maven Central AARs) and builds all four APKs
   (arm64-v8a, armeabi-v7a, x86_64, universal) -- no separate binary-fetching
   steps needed
3. APKs are uploaded as a downloadable workflow artifact **and** attached
   to an actual GitHub Release (tagged `v<versionName>`) on your repo --
   the Release page works as a plain public download link (no GitHub login
   needed to grab an APK), and it's also what Settings > Updates checks
   against for "is a newer VidDown build available"

So: trigger the workflow (push to `main`, or the "Run workflow" button under
Actions), wait for it to go green, download the artifact -- that's it, you
get real installable APKs without touching a terminal.

The only thing this **doesn't** cover is building **on your own computer**
directly (outside GitHub) -- see "Building locally" below if you want that
too; it's optional.

## Settings & in-app updates

A 4th tab (Settings) covers:

- **Download location** -- pick a folder via the system folder picker (SAF).
  Finished downloads get copied there instead of app-private storage;
  History's Play/Share buttons work either way.
- **WiFi-only downloads** -- a real `WorkManager` constraint
  (`NetworkType.UNMETERED`), not just a UI toggle -- a queued download simply
  won't run until it's satisfied.
- **Max concurrent downloads** -- a 1-10 slider. Enforced by DownloadWorker
  polling the Room `downloads` table's running count against the limit
  (WorkManager's own concurrency is fixed at app startup and can't be
  resized at runtime), with a mutex so two workers can't both grab the same
  last free slot.
- **Theme** -- System / Light / Dark, applied immediately app-wide.
- **Notifications** (its own sub-screen) -- separate toggles for
  download-progress and download-completed notifications.
- **Clear cache & history** -- wipes History's completed/failed/cancelled
  list. Doesn't touch already-downloaded files on disk.
- **Updates** (its own sub-screen) -- an auto-check-on-startup toggle, plus:
  - **App updates** check `UpdateRepos.APP_REPO` (`saimum10/VidDown`) for a
    newer GitHub release, download the APK, and hand it to the system
    installer. For this to find anything, your releases need `.apk` files
    matching each ABI (e.g. `viddown-arm64-v8a.apk`) or one containing
    `universal`. It never installs silently -- the user always confirms via
    Android's own installer screen.
  - **yt-dlp** has its own single "Update" button, using the library's
    built-in `YoutubeDL.updateYoutubeDL()` (checks the latest GitHub release
    and swaps the binary in place if newer).
  - ffmpeg/aria2c no longer have update rows -- they're bundled at build
    time via Gradle now, not hot-swappable binaries fetched at runtime.
    "Updating" them means bumping the dependency version in
    `app/build.gradle.kts` and shipping a new release.

## Known simplifications (flagged, not hidden)

- **`looksDownloadable()` is a URL-substring heuristic**, not a real page
  scan -- fast and good enough to decide when to show the download FAB, but
  it'll miss a few sites and can false-positive on unrelated pages that
  happen to match (e.g. a non-video `youtube.com` URL).
- **No playlist support yet** -- `extractInfo()` grabs whatever yt-dlp's
  `--dump-json` returns for the URL as given; a playlist/feed URL isn't
  expanded into its individual entries.
- **The final downloaded path is read from yt-dlp's own `--print
  after_move:filepath` output** (the line printed after all
  postprocessing/moves are done) rather than predicted ahead of time --
  reliable in practice, but depends on that being the last non-blank stdout
  line.
- **Signing:** `release` build type reads a keystore from environment
  variables (`VIDDOWN_KEYSTORE_PATH` etc.) if set, otherwise builds
  unsigned. Debug builds are unaffected.

## Building locally

Optional -- only needed if you want to build directly on your own machine
instead of using GitHub Actions. First generate the Gradle wrapper (I
couldn't do this in the sandbox -- no network access to
`services.gradle.org`):

```
gradle wrapper --gradle-version 8.9
```

(Or just open the project in Android Studio -- it adds the wrapper
automatically.) Then:

```
./gradlew assembleDebug
```

Outputs land in `app/build/outputs/apk/` -- one APK per ABI plus a
`universal` one. yt-dlp/ffmpeg/aria2c are pulled in automatically as Gradle
dependencies, no manual binary setup needed.
