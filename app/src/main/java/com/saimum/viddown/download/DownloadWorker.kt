package com.saimum.viddown.download

import android.content.Context
import androidx.core.app.NotificationManagerCompat
import androidx.core.net.toUri
import androidx.documentfile.provider.DocumentFile
import androidx.work.CoroutineWorker
import androidx.work.ForegroundInfo
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.saimum.viddown.data.AppDatabase
import com.saimum.viddown.data.DownloadStatus
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.engine.CookieBridge
import com.saimum.viddown.engine.YtDlpEngine
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import java.io.File

class DownloadWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val downloadId = inputData.getLong(KEY_DOWNLOAD_ID, -1L)
        val url = inputData.getString(KEY_URL) ?: return Result.failure()
        val mode = inputData.getString(KEY_MODE) ?: "video"
        val heightCap = inputData.getInt(KEY_HEIGHT_CAP, -1).takeIf { it > 0 }
        val mp3Bitrate = inputData.getInt(KEY_MP3_BITRATE, -1).takeIf { it > 0 }
        val formatId = inputData.getString(KEY_FORMAT_ID)
        val title = inputData.getString(KEY_TITLE) ?: "video"

        val dao = AppDatabase.get(applicationContext).downloadDao()
        val entry = dao.getById(downloadId) ?: return Result.failure()

        val settings = SettingsRepository(applicationContext)
        val progressNotifsEnabled = settings.progressNotificationsEnabled.first()
        val completedNotifsEnabled = settings.completedNotificationsEnabled.first()

        // Foreground status has to be claimed up front, even while this
        // download is just waiting for a concurrency slot below -- otherwise
        // a long queue wait risks hitting Android's background-execution
        // time limit and getting killed before it ever starts downloading.
        setForeground(makeForegroundInfo(title, 0))

        // Wait for a free slot per Settings > "Max concurrent downloads".
        // Re-reads the limit every poll, so moving the slider while
        // downloads are already queued takes effect immediately instead of
        // only applying to downloads started after the change.
        while (true) {
            if (CancelledDownloads.isRequested(downloadId)) {
                // Cancelled from the UI while still queued -- DownloadRepository.cancel()
                // already wrote CANCELLED to the DB directly, so there's nothing to
                // download or clean up here; just clear the flag and stop.
                CancelledDownloads.clear(downloadId)
                NotificationManagerCompat.from(applicationContext).cancel(downloadId.toInt())
                return Result.failure()
            }
            val maxConcurrent = settings.maxConcurrentDownloads.first()
            val gotSlot = DownloadConcurrencyGate.withSlotCheck {
                if (dao.countRunning() < maxConcurrent) {
                    dao.update(entry.copy(status = DownloadStatus.RUNNING))
                    true
                } else {
                    false
                }
            }
            if (gotSlot) break
            delay(1500)
        }

        val downloadsDir = File(applicationContext.getExternalFilesDir(null), "VidDown").apply { mkdirs() }
        val safeName = title.replace(Regex("[\\\\/:*?\"<>|]"), "_").take(80)
        val outputNoExt = File(downloadsDir, safeName).absolutePath

        // Generated fresh here (not back at enqueue time) so a download that
        // sat in the queue for a while still uses the session's current
        // cookies rather than a stale snapshot -- see CookieBridge.kt.
        val cookiesPath = CookieBridge.exportCookiesForUrl(applicationContext, url)

        // yt-dlp's progress parser can re-report the same percent several
        // times in a row (extra output lines, postprocessing steps, etc).
        // FIX: onProgress used to do a blocking DB write (runBlocking) *and*
        // post a notification on every single call, even for a repeat of the
        // same percent -- on a fast connection that's many redundant blocking
        // writes per second on the same thread that's driving the download,
        // which is a big part of why downloads/the app could feel sluggish.
        // Only actually do that work when the percent has moved.
        var lastWrittenPercent = -1
        try {
            val result = YtDlpEngine.download(
                url = url,
                outputPathNoExt = outputNoExt,
                mode = mode,
                heightCap = heightCap,
                formatId = formatId,
                mp3Bitrate = mp3Bitrate,
                downloadId = downloadId,
                cookiesPath = cookiesPath,
                onProgress = { percent ->
                    setProgressAsync(workDataOf(KEY_PROGRESS_PERCENT to percent))
                    if (percent != lastWrittenPercent) {
                        lastWrittenPercent = percent
                        if (progressNotifsEnabled) notifySafely(downloadId.toInt(), title, percent)
                        runBlocking { dao.update(entry.copy(status = DownloadStatus.RUNNING, progressPercent = percent)) }
                    }
                }
            )

            // A Cancel tap from the UI already wrote CANCELLED to the DB itself
            // (DownloadRepository.cancel()) and killed the underlying yt-dlp
            // process by id, which is what makes `result` a failure here --
            // either way, this flag is the source of truth for "the user
            // cancelled this", so it takes priority over whatever `result`
            // says and is cleared once handled.
            if (CancelledDownloads.isRequested(downloadId)) {
                CancelledDownloads.clear(downloadId)
                dao.update(entry.copy(status = DownloadStatus.CANCELLED, errorMessage = null))
                NotificationManagerCompat.from(applicationContext).cancel(downloadId.toInt())
                return Result.failure()
            }

            return result.fold(
                onSuccess = { r ->
                    val finalPath = r.path?.let { moveToUserFolderIfConfigured(it) } ?: r.path
                    dao.update(
                        entry.copy(
                            status = DownloadStatus.COMPLETED,
                            filePath = finalPath,
                            progressPercent = 100
                        )
                    )
                    NotificationManagerCompat.from(applicationContext).cancel(downloadId.toInt())
                    if (completedNotifsEnabled) notifyCompletedSafely(downloadId.toInt(), title)
                    Result.success()
                },
                onFailure = { e ->
                    dao.update(entry.copy(status = DownloadStatus.FAILED, errorMessage = e.message))
                    NotificationManagerCompat.from(applicationContext).cancel(downloadId.toInt())
                    Result.failure()
                }
            )
        } finally {
            // The cookie file holds live session credentials -- never leave
            // it sitting in cache longer than this one call needs it for.
            CookieBridge.delete(cookiesPath)
        }
    }

    /**
     * If Settings > "Download location" has a SAF folder configured, copies the
     * finished file there and returns its content:// URI (as a string) instead
     * of the plain filesystem path. History's open/share actions know to treat
     * a "content://" filePath differently from a plain one -- see HistoryScreen.
     * Falls back to the original path on any failure (permission revoked, etc.)
     * so a download is never "lost" over a folder-move error.
     */
    private suspend fun moveToUserFolderIfConfigured(sourcePath: String): String {
        val folderUriString = SettingsRepository(applicationContext).downloadFolderUri.first() ?: return sourcePath
        val sourceFile = File(sourcePath)
        if (!sourceFile.exists()) return sourcePath

        return runCatching {
            val treeUri = folderUriString.toUri()
            val treeDoc = DocumentFile.fromTreeUri(applicationContext, treeUri)
                ?: return@runCatching sourcePath
            val mimeType = applicationContext.contentResolver.getType(
                android.net.Uri.fromFile(sourceFile)
            ) ?: "application/octet-stream"

            val newDoc = treeDoc.createFile(mimeType, sourceFile.name) ?: return@runCatching sourcePath
            applicationContext.contentResolver.openOutputStream(newDoc.uri)?.use { out ->
                sourceFile.inputStream().use { it.copyTo(out) }
            }
            sourceFile.delete()
            newDoc.uri.toString()
        }.getOrDefault(sourcePath)
    }

    /** NotificationManagerCompat.notify() can throw SecurityException if POST_NOTIFICATIONS
     *  was denied on API 33+ -- the download itself should keep going either way. */
    private fun notifySafely(id: Int, title: String, percent: Int) {
        runCatching {
            val notification = NotificationHelper.progressNotification(applicationContext, title, percent).build()
            NotificationManagerCompat.from(applicationContext).notify(id, notification)
        }
    }

    private fun notifyCompletedSafely(id: Int, title: String) {
        runCatching {
            val notification = NotificationHelper.completedNotification(applicationContext, title).build()
            NotificationManagerCompat.from(applicationContext).notify(id, notification)
        }
    }

    private fun makeForegroundInfo(title: String, percent: Int): ForegroundInfo {
        val notification = NotificationHelper.progressNotification(applicationContext, title, percent).build()
        val downloadId = inputData.getLong(KEY_DOWNLOAD_ID, 0L).toInt()
        return ForegroundInfo(downloadId, notification)
    }

    companion object {
        const val KEY_DOWNLOAD_ID = "download_id"
        const val KEY_URL = "url"
        const val KEY_MODE = "mode"
        const val KEY_HEIGHT_CAP = "height_cap"
        const val KEY_FORMAT_ID = "format_id"
        const val KEY_MP3_BITRATE = "mp3_bitrate"
        const val KEY_TITLE = "title"
        const val KEY_PROGRESS_PERCENT = "progress_percent"
    }
}
