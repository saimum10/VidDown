package com.saimum.viddown.data.model

/**
 * A concrete, selectable download quality shown in the format picker.
 *
 * These are built from the real formats yt-dlp returned for a given URL
 * (see [DownloadOptions.build]) -- a tier only ever appears when the
 * source actually offers something at that level, so the list adapts to
 * whatever the extractor found rather than a fixed hardcoded catalog.
 */
sealed class DownloadOption {
    abstract val label: String
    abstract val subtitle: String?
    abstract val approxSizeBytes: Long?
    abstract val badge: String?

    data class Video(
        val heightCap: Int,
        /** The specific format id yt-dlp already told us matches this tier
         *  (see DownloadOptions.build) -- null only for the degenerate
         *  fallback case where nothing matched exactly. */
        val formatId: String?,
        override val label: String,
        override val subtitle: String?,
        override val approxSizeBytes: Long?,
        override val badge: String?
    ) : DownloadOption()

    data class Audio(
        /** null = fast passthrough, no re-encoding. */
        val bitrateKbps: Int?,
        val formatId: String?,
        override val label: String,
        override val subtitle: String?,
        override val approxSizeBytes: Long?,
        override val badge: String?
    ) : DownloadOption()
}

object DownloadOptions {

    private data class VideoTier(
        val heightCap: Int, val label: String, val subtitle: String, val badge: String?, val curated: Boolean
    )

    private data class AudioTier(
        val bitrateKbps: Int?, val label: String, val subtitle: String, val badge: String?, val curated: Boolean
    )

    private val VIDEO_TIERS = listOf(
        VideoTier(144, "Fast (144p)", "Lowest clarity, smallest file", "Low", curated = false),
        VideoTier(240, "Fast (240p)", "Small file, fine for a quick watch", null, curated = true),
        VideoTier(360, "Fast (360p)", "Balanced size and clarity", null, curated = false),
        VideoTier(480, "Fast (480p)", "Balanced size and clarity", null, curated = false),
        VideoTier(720, "High quality (720p)", "Sharp picture, larger file", null, curated = true),
        VideoTier(1080, "High quality (1080p)", "Full HD, sharp picture", null, curated = false),
        VideoTier(1440, "Ultra HD (1440p / 2K)", "Very sharp, very large file", null, curated = false),
        VideoTier(2160, "Ultra HD (2160p / 4K)", "Extremely sharp, extremely large file", "Slow", curated = false),
        VideoTier(4320, "Ultra HD (4320p / 8K)", "Maximum detail, massive file size", "Slow", curated = false),
    )

    private val AUDIO_TIERS = listOf(
        AudioTier(null, "Fast", "Original audio track, no re-encoding -- quickest option", null, curated = true),
        AudioTier(70, "Classic MP3 (70K)", "Small file, plays on any Bluetooth speaker or car stereo", "Low", curated = false),
        AudioTier(128, "Classic MP3 (128K)", "Plays on any Bluetooth speaker or car stereo", null, curated = false),
        AudioTier(160, "Classic MP3", "Standard MP3 quality, works everywhere", null, curated = true),
        AudioTier(320, "Classic MP3 (320K)", "Highest MP3 quality, takes longer to prepare", "Slow", curated = false),
    )

    /** Returns (curated shortlist, full list) -- mirrors the picker's
     *  "few defaults, then More formats -> All" layout. */
    fun build(info: VideoInfo): Pair<List<DownloadOption>, List<DownloadOption>> {
        val maxHeight = info.formats.filter { it.hasVideo }.mapNotNull { it.heightPx }.maxOrNull() ?: 0
        val hasAudio = info.formats.any { it.hasAudio }

        val curated = mutableListOf<DownloadOption>()
        val all = mutableListOf<DownloadOption>()

        if (hasAudio) {
            val bestAudioOnly = info.formats
                .filter { it.hasAudio && !it.hasVideo }
                .maxByOrNull { it.audioBitrateKbps ?: 0.0 }
            for (tier in AUDIO_TIERS) {
                val option = DownloadOption.Audio(
                    bitrateKbps = tier.bitrateKbps,
                    // Passthrough ("Fast") targets the specific audio-only
                    // format we already found -- re-encoded tiers still
                    // resolve generically since ffmpeg picks the source itself.
                    formatId = if (tier.bitrateKbps == null) bestAudioOnly?.formatId else null,
                    label = tier.label,
                    subtitle = tier.subtitle,
                    approxSizeBytes = approxAudioSize(info, tier.bitrateKbps),
                    badge = tier.badge
                )
                all.add(option)
                if (tier.curated) curated.add(option)
            }
        }

        val availableVideoTiers = VIDEO_TIERS.filter { maxHeight >= it.heightCap }
        for (tier in availableVideoTiers) {
            val match = closestVideoFormat(info, tier.heightCap)
            val option = DownloadOption.Video(
                heightCap = tier.heightCap,
                formatId = match?.formatId,
                label = tier.label,
                subtitle = tier.subtitle,
                approxSizeBytes = match?.filesizeBytes,
                badge = tier.badge
            )
            all.add(option)
            if (tier.curated) curated.add(option)
        }

        // Degenerate case: the source caps out below both curated video
        // tiers (e.g. a clip that only goes up to 144p) -- still offer
        // whatever's actually there instead of showing zero video options.
        if (availableVideoTiers.isNotEmpty() && curated.none { it is DownloadOption.Video }) {
            val fallback = availableVideoTiers.last()
            val match = closestVideoFormat(info, fallback.heightCap)
            curated.add(
                DownloadOption.Video(
                    heightCap = fallback.heightCap,
                    formatId = match?.formatId,
                    label = fallback.label,
                    subtitle = fallback.subtitle,
                    approxSizeBytes = match?.filesizeBytes,
                    badge = fallback.badge
                )
            )
        }

        return curated to all
    }

    /** The actual extracted format closest to (at or below) this tier's
     *  height -- carries both its real id and its real size, since yt-dlp
     *  already told us both for that concrete format. */
    private fun closestVideoFormat(info: VideoInfo, heightCap: Int): VideoFormat? =
        info.formats
            .filter { it.hasVideo && (it.heightPx ?: 0) in 1..heightCap }
            .maxByOrNull { it.heightPx ?: 0 }

    /** MP3 re-encoding produces a file yt-dlp hasn't created yet, so this
     *  estimates it from bitrate * duration -- the same approximation any
     *  downloader has to make before the encode actually happens. */
    private fun approxAudioSize(info: VideoInfo, bitrateKbps: Int?): Long? {
        val durationSeconds = info.durationSeconds ?: return null
        val kbps = bitrateKbps?.toDouble()
            ?: info.formats.filter { it.hasAudio && !it.hasVideo }.mapNotNull { it.audioBitrateKbps }.maxOrNull()
            ?: 128.0
        return (kbps * durationSeconds * 1000.0 / 8.0).toLong()
    }
}
