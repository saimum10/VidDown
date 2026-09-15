package com.saimum.viddown.download

import android.content.Context
import androidx.work.Constraints
import androidx.work.Data
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import com.saimum.viddown.data.AppDatabase
import com.saimum.viddown.data.DownloadEntity
import com.saimum.viddown.data.DownloadStatus
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.data.model.DownloadOption
import com.saimum.viddown.data.model.VideoInfo
import com.saimum.viddown.engine.YtDlpEngine
import kotlinx.coroutines.flow.first

class DownloadRepository(private val context: Context) {

    private val dao = AppDatabase.get(context).downloadDao()
    private val settings = SettingsRepository(context)

    fun observeActive() = dao.observeActive()
    fun observeHistory() = dao.observeHistory()

    suspend fun enqueue(sourceUrl: String, info: VideoInfo, option: DownloadOption) {
        val entity = DownloadEntity(
            sourceUrl = sourceUrl,
            title = info.title,
            thumbnailUrl = info.thumbnailUrl,
            formatLabel = option.label,
            isAudioOnly = option is DownloadOption.Audio
        )
        val id = dao.insert(entity)

        val dataBuilder = Data.Builder()
            .putLong(DownloadWorker.KEY_DOWNLOAD_ID, id)
            .putString(DownloadWorker.KEY_URL, sourceUrl)
            .putString(DownloadWorker.KEY_TITLE, info.title)

        when (option) {
            is DownloadOption.Video -> {
                dataBuilder
                    .putString(DownloadWorker.KEY_MODE, "video")
                    .putInt(DownloadWorker.KEY_HEIGHT_CAP, option.heightCap)
                option.formatId?.let { dataBuilder.putString(DownloadWorker.KEY_FORMAT_ID, it) }
            }
            is DownloadOption.Audio -> {
                if (option.bitrateKbps == null) {
                    dataBuilder.putString(DownloadWorker.KEY_MODE, "audio_fast")
                    option.formatId?.let { dataBuilder.putString(DownloadWorker.KEY_FORMAT_ID, it) }
                } else {
                    dataBuilder
                        .putString(DownloadWorker.KEY_MODE, "audio_mp3")
                        .putInt(DownloadWorker.KEY_MP3_BITRATE, option.bitrateKbps)
                }
            }
        }
        val data = dataBuilder.build()

        // Settings > "WiFi-only downloads" becomes a real WorkManager constraint:
        // the worker simply won't run (and will resume automatically) until an
        // unmetered network is available, rather than the app polling for it.
        val wifiOnly = settings.wifiOnly.first()
        val constraints = Constraints.Builder()
            .setRequiredNetworkType(if (wifiOnly) NetworkType.UNMETERED else NetworkType.CONNECTED)
            .build()

        val request = OneTimeWorkRequestBuilder<DownloadWorker>()
            .setInputData(data)
            .setConstraints(constraints)
            .addTag(tagFor(id))
            .build()

        WorkManager.getInstance(context).enqueue(request)
    }

    suspend fun delete(id: Long) = dao.delete(id)

    suspend fun clearHistory() = dao.clearFinished()

    /**
     * Cancels a queued or in-progress download. Covers both cases:
     *  - still QUEUED (never dequeued by WorkManager, or waiting in
     *    DownloadWorker's own concurrency-slot loop) -- cancelAllWorkByTag
     *    stops a not-yet-dequeued one from ever starting, and
     *    CancelledDownloads catches the "waiting for a slot" case; either
     *    way the DB write below is what actually marks it CANCELLED, since
     *    DownloadWorker.doWork() never gets to the point of downloading.
     *  - already RUNNING -- YtDlpEngine.cancel() kills the real underlying
     *    yt-dlp OS process by id, so the transfer actually stops instead of
     *    the worker just detaching from something still running underneath.
     */
    suspend fun cancel(id: Long) {
        CancelledDownloads.request(id)
        WorkManager.getInstance(context).cancelAllWorkByTag(tagFor(id))
        YtDlpEngine.cancel(id)
        dao.getById(id)?.let { dao.update(it.copy(status = DownloadStatus.CANCELLED)) }
    }
}

private fun tagFor(id: Long) = "download_$id"
