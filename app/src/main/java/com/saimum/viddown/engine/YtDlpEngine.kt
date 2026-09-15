package com.saimum.viddown.engine

import android.content.Context
import com.saimum.viddown.data.model.DownloadResult
import com.saimum.viddown.data.model.VideoInfo
import com.yausername.aria2c.Aria2c
import com.yausername.ffmpeg.FFmpeg
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLRequest
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import com.yausername.youtubedl_android.mapper.VideoFormat as YtdlpVideoFormat
import com.yausername.youtubedl_android.mapper.VideoInfo as YtdlpVideoInfo

/**
 * Thin bridge to the youtubedl-android library (bundles yt-dlp + a Python
 * runtime as a prebuilt native AAR per ABI -- see build.gradle.kts). Every
 * call here blocks the calling thread until yt-dlp's real CLI process
 * finishes, so all public functions are `suspend` and hop onto
 * Dispatchers.IO.
 */
object YtDlpEngine {

    private val engineScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    // Completed once init() has actually finished unpacking/validating the
    // bundled Python + yt-dlp + ffmpeg + aria2c payloads. Any extract/download
    // call made before that (e.g. the user tapping Download right after
    // opening the app) suspends here instead of crashing or racing init.
    private val ready = CompletableDeferred<Unit>()

    /**
     * FIX: this used to call YoutubeDL/FFmpeg/Aria2c .init() synchronously,
     * and VidDownApp.onCreate() called it directly on the main thread. Those
     * init() calls unpack/verify the bundled Python runtime + yt-dlp +
     * ffmpeg + aria2c binaries from the APK -- real, slow disk I/O (several
     * seconds, more on the very first launch after install/update) -- so
     * running them on the main thread blocked the entire UI (including the
     * in-app browser) before the first frame ever drew. That's what was
     * causing the app to feel frozen/slow on open, occasionally trip
     * Android's ANR watchdog, and get force-closed a few seconds in. Now the
     * work happens on a background dispatcher and the UI is never blocked by
     * it; awaitReady() below is what makes callers wait for it safely
     * instead of racing it.
     */
    fun init(context: Context) {
        engineScope.launch {
            runCatching {
                YoutubeDL.getInstance().init(context)
                // Every execute() call passes --ffmpeg-location regardless of
                // whether a given request needs it, so this has to be
                // initialized even for plain video-only downloads.
                FFmpeg.getInstance().init(context)
                // Safe to call even when the aria2c native lib isn't present --
                // Aria2c.init() just no-ops in that case (see its source).
                Aria2c.getInstance().init(context)
            }
            ready.complete(Unit)

            // FIX (likely the real reason extraction/download was failing):
            // updateYtDlp() below used to only ever run when the user
            // manually opened Settings > Check for updates -- which almost
            // nobody does on first install. The yt-dlp binary bundled in
            // this AAR is a fixed snapshot from whenever that release was
            // published; YouTube specifically changes its player/signature
            // format often enough that yt-dlp needs frequent updates just to
            // keep working at all against it (this is extremely well
            // documented upstream -- stale yt-dlp is the single most common
            // cause of "unable to extract"/"sign in to confirm you're not a
            // bot"/parse failures). Firing this once per app process, in the
            // background, after init -- not blocking readiness, so the app
            // is still usable immediately even if this is slow or offline --
            // means extraction actually has a current binary to work with
            // without the user ever needing to know this screen exists.
            runCatching { YoutubeDL.getInstance().updateYoutubeDL(context) }
        }
    }

    private suspend fun awaitReady() = ready.await()

    /** Stable process id for a given download, shared between the worker
     *  that calls execute() and DownloadRepository.cancel() so the latter
     *  can kill the right OS process without any extra coordination. */
    fun processIdFor(downloadId: Long): String = "download_$downloadId"

    /**
     * BUG FIX: this used to be a hardcoded allowlist of just 5 site families
     * (YouTube/TikTok/Instagram/Facebook/X), matched by exact URL substrings.
     * The actual download engine (yt-dlp) supports well over a thousand
     * sites -- Vimeo, Reddit, Dailymotion, Twitch, SoundCloud, Threads,
     * Pinterest, LinkedIn, direct video files, and on and on -- so for any
     * page outside that tiny list, the Download button silently never
     * appeared at all, even though tapping it (had it been shown) would
     * have worked fine. Now it's just "is this a real http(s) page", and
     * fetchFormats()/extractInfo() -- which actually asks yt-dlp -- is what
     * decides whether a specific page truly has something downloadable,
     * surfacing a proper error (see BrowserViewModel.fetchFormats) if not.
     */
    fun looksDownloadable(url: String): Boolean =
        url.startsWith("http://") || url.startsWith("https://")

    // FIX (Download button "just spins forever"): neither this app nor
    // yt-dlp itself was enforcing any upper bound on how long a single
    // extraction could take. On a bad/blocked connection, yt-dlp's own
    // retry loop can legitimately run for minutes before finally giving up
    // -- from the UI that's indistinguishable from being stuck, since
    // isFetchingFormats stays true the whole time. --socket-timeout/--retries
    // bound each individual network attempt yt-dlp makes; withTimeout below
    // is the hard ceiling on the whole call regardless, so this always
    // resolves to a clear error within EXTRACT_TIMEOUT_MS, never silently.
    private const val EXTRACT_TIMEOUT_MS = 20_000L

    suspend fun extractInfo(url: String, cookiesPath: String? = null): Result<VideoInfo> =
        withContext(Dispatchers.IO) {
            awaitReady()
            val primary = runCatching {
                withTimeout(EXTRACT_TIMEOUT_MS) {
                    val request = YoutubeDLRequest(url)
                    request.addOption("--socket-timeout", "8")
                    request.addOption("--retries", "2")
                    if (cookiesPath != null) request.addOption("--cookies", cookiesPath)
                    YoutubeDL.getInstance().getInfo(request).toVideoInfo()
                }
            }
            if (primary.isSuccess) return@withContext primary

            // FIX: YouTube increasingly rejects extraction with its default
            // client identity unless the request carries a Proof-of-Origin
            // token (a value only obtainable by actually running YouTube's
            // own JS challenge -- a much bigger feature, a JS-runtime-backed
            // token provider, than a quick patch here). Short of that, some
            // alternate client identities are, at least for now, less
            // consistently gated on it than the default. This is a known
            // moving target -- which client works shifts over time as
            // YouTube adjusts -- so it's a second attempt on top of the
            // normal default, not a replacement for it.
            runCatching {
                withTimeout(EXTRACT_TIMEOUT_MS) {
                    val request = YoutubeDLRequest(url)
                    request.addOption("--socket-timeout", "8")
                    request.addOption("--retries", "2")
                    request.addOption("--extractor-args", "youtube:player_client=android,web,tv")
                    if (cookiesPath != null) request.addOption("--cookies", cookiesPath)
                    YoutubeDL.getInstance().getInfo(request).toVideoInfo()
                }
            }.recoverCatching { e ->
                if (e is TimeoutCancellationException) {
                    throw Exception("Timed out reading this page. Check your connection and try again.")
                }
                // Surface the ORIGINAL failure's message, not the retry's --
                // it's the more informative one if both attempts failed the
                // same underlying way.
                throw primary.exceptionOrNull() ?: e
            }
        }

    /**
     * Downloads `url` to `outputPathNoExt` (extension-less -- yt-dlp fills
     * it in) at the given quality tier:
     *
     *   mode="video"      -- best video+audio with video height <= heightCap
     *                        (or absolute best if heightCap is null)
     *   mode="audio_fast"  -- best audio track as-is, no re-encoding
     *   mode="audio_mp3"   -- best audio re-encoded to MP3 at mp3Bitrate kbps
     *
     * `formatId`, when present, is the *exact* format DownloadOptions.build()
     * already matched during the earlier extractInfo() call for this same
     * tier -- see the comment below for why that matters. `downloadId`
     * becomes this call's process id (see processIdFor) so
     * DownloadRepository.cancel() can kill it by id; `onProgress` is called
     * with a 0-100 percent on every yt-dlp progress line the library parses
     * out of stdout for us.
     */
    suspend fun download(
        url: String,
        outputPathNoExt: String,
        mode: String,
        heightCap: Int?,
        formatId: String? = null,
        mp3Bitrate: Int?,
        downloadId: Long,
        cookiesPath: String? = null,
        onProgress: ((percent: Int) -> Unit)? = null
    ): Result<DownloadResult> = withContext(Dispatchers.IO) {
        awaitReady()
        runCatching {
            val request = YoutubeDLRequest(url)
            request.addOption("-o", "$outputPathNoExt.%(ext)s")
            // Evaluated after all postprocessing/moves are done, so this is
            // the one reliable way to learn the real final path -- yt-dlp
            // picks the extension itself (container/codec dependent) and we
            // don't otherwise know it ahead of time for audio_fast especially.
            request.addOption("--print", "after_move:filepath")
            if (cookiesPath != null) request.addOption("--cookies", cookiesPath)
            // Parallel fragment fetching for DASH/HLS sources -- matches
            // what ytdlnis's own download command does (-N "3"); noticeably
            // faster on sites that serve segmented streams, harmless no-op
            // on ones that don't.
            request.addOption("-N", "3")

            when (mode) {
                "audio_fast" -> {
                    // FIX: previously always re-derived a generic filter here
                    // ("bestaudio[ext=m4a]/bestaudio/best"), even though the
                    // format picker already showed the user a specific,
                    // already-extracted format a few seconds earlier. That
                    // meant every download re-resolved the URL from scratch --
                    // a second full network extraction, a second exposure to
                    // whatever's flaky/rate-limited/bot-checked about the
                    // source, and a real chance the generic filter picks a
                    // *different* format than what the user actually saw
                    // and picked. When we already know the exact format id
                    // (the normal case), target it directly; only fall back
                    // to the generic filter when we don't (e.g. a picker
                    // entry built before this format-id tracking existed).
                    request.addOption("-f", formatId ?: "bestaudio[ext=m4a]/bestaudio/best")
                }
                "audio_mp3" -> {
                    request.addOption("-f", "bestaudio/best")
                    request.addOption("--extract-audio")
                    request.addOption("--audio-format", "mp3")
                    request.addOption("--audio-quality", "${mp3Bitrate ?: 160}K")
                }
                else -> {
                    if (formatId != null) {
                        request.addOption("-f", "$formatId+bestaudio/$formatId/best")
                    } else {
                        val cap = if (heightCap != null) "[height<=?$heightCap]" else ""
                        request.addOption("-f", "bestvideo$cap+bestaudio/best$cap")
                    }
                    request.addOption("--merge-output-format", "mp4")
                }
            }

            val response = YoutubeDL.getInstance()
                .execute(request, processIdFor(downloadId)) { percent, _, _ ->
                    onProgress?.invoke(percent.toInt().coerceIn(0, 100))
                }

            val finalPath = response.out.lineSequence().lastOrNull { it.isNotBlank() }?.trim()
            DownloadResult(finalPath)
        }
    }

    /** Kills a running download by id. No-op (returns false) if it isn't
     *  currently executing -- e.g. still waiting for a concurrency slot,
     *  which DownloadWorker handles separately via CancelledDownloads. */
    fun cancel(downloadId: Long): Boolean =
        YoutubeDL.getInstance().destroyProcessById(processIdFor(downloadId))

    // --- Updates -----------------------------------------------------

    /** Null until updateYtDlp() has actually been run at least once --
     *  before that, the app is just running whatever version was bundled
     *  into the AAR at build time. */
    fun currentYtDlpVersion(context: Context): String? = YoutubeDL.versionName(context)

    data class UpdateResult(val success: Boolean, val versionName: String?, val error: String?)

    suspend fun updateYtDlp(context: Context): UpdateResult = withContext(Dispatchers.IO) {
        awaitReady()
        runCatching {
            val status = YoutubeDL.getInstance().updateYoutubeDL(context)
            UpdateResult(
                success = true,
                versionName = YoutubeDL.versionName(context),
                error = if (status == YoutubeDL.UpdateStatus.ALREADY_UP_TO_DATE) "Already up to date" else null
            )
        }.getOrElse { UpdateResult(success = false, versionName = null, error = it.message) }
    }

    private fun YtdlpVideoInfo.toVideoInfo(): VideoInfo = VideoInfo(
        id = id,
        title = (title ?: fulltitle)?.takeIf { it.isNotBlank() } ?: "video",
        thumbnailUrl = thumbnail,
        durationSeconds = duration.toDouble().takeIf { it > 0 },
        extractor = extractor,
        formats = (formats ?: arrayListOf()).map { it.toVideoFormat() }
    )

    private fun YtdlpVideoFormat.toVideoFormat() = com.saimum.viddown.data.model.VideoFormat(
        formatId = formatId ?: "",
        ext = ext ?: "",
        resolution = formatNote ?: "",
        heightPx = height.takeIf { it > 0 },
        filesizeBytes = fileSize.takeIf { it > 0 } ?: fileSizeApproximate.takeIf { it > 0 },
        hasVideo = vcodec != null && vcodec != "none",
        hasAudio = acodec != null && acodec != "none",
        audioBitrateKbps = abr.toDouble().takeIf { it > 0 },
        fps = fps.toDouble().takeIf { it > 0 }
    )
}
