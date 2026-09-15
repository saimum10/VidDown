package com.saimum.viddown.update

/**
 * Repo the in-app updater checks against for new VidDown app releases.
 * For the "check for app update" button to find anything, that repo's
 * releases need .apk assets named per-ABI (e.g. "viddown-arm64-v8a.apk")
 * or containing "universal".
 *
 * ffmpeg and aria2c used to have their own entries here (fetched at CI
 * build time and hot-swappable at runtime) -- they're now plain Gradle
 * dependencies (youtubedl-android AARs), so "updating" them just means
 * bumping a version number in build.gradle.kts and shipping a new release.
 */
object UpdateRepos {
    const val APP_REPO = "saimum10/VidDown"
}
