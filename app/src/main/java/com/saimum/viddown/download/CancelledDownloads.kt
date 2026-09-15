package com.saimum.viddown.download

import java.util.concurrent.ConcurrentHashMap

/**
 * Cross-cutting cancel flag, keyed by download id.
 *
 * A Cancel tap (DownloadRepository, main process) can reach an in-progress
 * download directly -- YtDlpEngine.cancel() kills its real OS process by
 * id -- but a download that's still waiting for a free slot in
 * DownloadWorker's own concurrency-limit loop hasn't started a process yet,
 * so there's nothing to kill. This flag covers that gap: DownloadWorker
 * checks it on every wait-loop poll and bails out immediately if set.
 *
 * DownloadWorker also checks this flag right after YtDlpEngine.download()
 * returns (success, failure, or killed-mid-transfer all land here) so a
 * user-cancelled download is always recorded as CANCELLED rather than
 * FAILED, then clears the flag.
 */
object CancelledDownloads {
    private val ids = ConcurrentHashMap.newKeySet<Long>()

    fun request(id: Long) {
        ids.add(id)
    }

    fun isRequested(id: Long): Boolean = ids.contains(id)

    fun clear(id: Long) {
        ids.remove(id)
    }
}
