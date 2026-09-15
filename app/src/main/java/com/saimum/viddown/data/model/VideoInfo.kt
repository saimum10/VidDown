package com.saimum.viddown.data.model

data class VideoInfo(
    val id: String?,
    val title: String,
    val thumbnailUrl: String?,
    val durationSeconds: Double?,
    val extractor: String?,
    val formats: List<VideoFormat>
)

data class VideoFormat(
    val formatId: String,
    val ext: String,
    val resolution: String,
    val heightPx: Int?,
    val filesizeBytes: Long?,
    val hasVideo: Boolean,
    val hasAudio: Boolean,
    val audioBitrateKbps: Double?,
    val fps: Double?
)

data class DownloadResult(val path: String?)
