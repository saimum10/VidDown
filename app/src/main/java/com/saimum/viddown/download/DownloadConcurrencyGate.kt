package com.saimum.viddown.download

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Serializes "check how many downloads are running, then claim RUNNING
 * status" across every DownloadWorker instance in this process.
 *
 * Settings > "Max concurrent downloads" is enforced by each worker polling
 * DownloadDao.countRunning() against that limit rather than through
 * WorkManager's own scheduling -- WorkManager's Configuration (and its
 * executor thread pool) is fixed at app startup and can't be resized at
 * runtime when the user moves the slider, so the limit has to be our own
 * check instead. Without this mutex, two waiting workers could both read
 * the same "1 slot free" count and both proceed, briefly exceeding the
 * limit by one.
 */
object DownloadConcurrencyGate {
    private val mutex = Mutex()

    suspend fun <T> withSlotCheck(block: suspend () -> T): T = mutex.withLock { block() }
}
