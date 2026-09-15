package com.saimum.viddown.data

import androidx.room.Entity
import androidx.room.PrimaryKey

enum class DownloadStatus { QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED }

@Entity(tableName = "downloads")
data class DownloadEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val sourceUrl: String,
    val title: String,
    val thumbnailUrl: String?,
    val formatLabel: String,
    val filePath: String? = null,
    val progressPercent: Int = 0,
    val status: DownloadStatus = DownloadStatus.QUEUED,
    val isAudioOnly: Boolean = false,
    val createdAt: Long = System.currentTimeMillis(),
    val errorMessage: String? = null
)
