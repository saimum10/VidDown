package com.saimum.viddown.data

import androidx.room.*
import kotlinx.coroutines.flow.Flow

@Dao
interface DownloadDao {

    @Insert
    suspend fun insert(entry: DownloadEntity): Long

    @Update
    suspend fun update(entry: DownloadEntity)

    @Query("DELETE FROM downloads WHERE id = :id")
    suspend fun delete(id: Long)

    @Query("SELECT * FROM downloads WHERE status = 'RUNNING' OR status = 'QUEUED' ORDER BY createdAt DESC")
    fun observeActive(): Flow<List<DownloadEntity>>

    // COMPLETED, FAILED, and CANCELLED all count as "finished" -- a failed or
    // cancelled download used to just vanish from both screens once it left
    // the active list, with no way to see it happened. Surfacing it here
    // instead so HistoryScreen can show what went wrong (or that it was
    // cancelled) rather than silently dropping the record.
    @Query("SELECT * FROM downloads WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED') ORDER BY createdAt DESC")
    fun observeHistory(): Flow<List<DownloadEntity>>

    @Query("SELECT * FROM downloads WHERE id = :id")
    suspend fun getById(id: Long): DownloadEntity?

    @Query("SELECT COUNT(*) FROM downloads WHERE status = 'RUNNING'")
    suspend fun countRunning(): Int

    @Query("DELETE FROM downloads WHERE status IN ('COMPLETED', 'FAILED', 'CANCELLED')")
    suspend fun clearFinished()
}
