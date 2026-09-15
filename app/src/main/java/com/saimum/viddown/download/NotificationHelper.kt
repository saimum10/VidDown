package com.saimum.viddown.download

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import androidx.core.app.NotificationCompat
import com.saimum.viddown.R

object NotificationHelper {
    const val CHANNEL_ID = "viddown_downloads"

    fun createChannel(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                context.getString(R.string.notif_channel_downloads),
                NotificationManager.IMPORTANCE_LOW
            )
            context.getSystemService(NotificationManager::class.java)
                .createNotificationChannel(channel)
        }
    }

    fun progressNotification(
        context: Context,
        title: String,
        percent: Int
    ): androidx.core.app.NotificationCompat.Builder =
        NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(title)
            .setOngoing(true)
            .setProgress(100, percent, percent <= 0)
            .setOnlyAlertOnce(true)

    fun completedNotification(
        context: Context,
        title: String
    ): androidx.core.app.NotificationCompat.Builder =
        NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_sys_download_done)
            .setContentTitle(title)
            .setContentText("Download complete")
            .setOngoing(false)
            .setAutoCancel(true)
}
