package com.saimum.viddown.data

import android.content.Context
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.intPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore by preferencesDataStore(name = "viddown_settings")
private val gson = Gson()
private val customShortcutListType = object : TypeToken<List<CustomShortcut>>() {}.type

enum class ThemeMode { SYSTEM, LIGHT, DARK }

/** A user-added tile on the Browser tab's home grid (the built-in YouTube /
 *  Facebook / X / Instagram tiles aren't stored here -- only ones the user
 *  adds via the "+" tile). */
data class CustomShortcut(val label: String, val url: String)

class SettingsRepository(private val context: Context) {

    private object Keys {
        val THEME_MODE = stringPreferencesKey("theme_mode")
        val WIFI_ONLY = booleanPreferencesKey("wifi_only")
        val DOWNLOAD_FOLDER_URI = stringPreferencesKey("download_folder_uri")
        val AUTO_UPDATE_ENABLED = booleanPreferencesKey("auto_update_enabled")
        val CUSTOM_SHORTCUTS = stringPreferencesKey("custom_shortcuts")
        val PROGRESS_NOTIFICATIONS = booleanPreferencesKey("progress_notifications")
        val COMPLETED_NOTIFICATIONS = booleanPreferencesKey("completed_notifications")
        val MAX_CONCURRENT_DOWNLOADS = intPreferencesKey("max_concurrent_downloads")
        val LAST_CLIPBOARD_LINK = stringPreferencesKey("last_clipboard_link")
    }

    val themeMode: Flow<ThemeMode> = context.dataStore.data.map { prefs ->
        runCatching { ThemeMode.valueOf(prefs[Keys.THEME_MODE] ?: ThemeMode.SYSTEM.name) }
            .getOrDefault(ThemeMode.SYSTEM)
    }

    val wifiOnly: Flow<Boolean> = context.dataStore.data.map { it[Keys.WIFI_ONLY] ?: false }

    /** Null means "use the app-private default folder" (no SAF folder picked yet). */
    val downloadFolderUri: Flow<String?> = context.dataStore.data.map { it[Keys.DOWNLOAD_FOLDER_URI] }

    val autoUpdateEnabled: Flow<Boolean> = context.dataStore.data.map { it[Keys.AUTO_UPDATE_ENABLED] ?: true }

    val customShortcuts: Flow<List<CustomShortcut>> = context.dataStore.data.map { prefs ->
        parseShortcuts(prefs[Keys.CUSTOM_SHORTCUTS])
    }

    /** Whether the ongoing notification updates with a live percentage while
     *  downloading. Android still requires *a* notification to exist while
     *  the download's foreground service runs -- this can't be turned off
     *  entirely, only whether it ticks with progress or stays static. */
    val progressNotificationsEnabled: Flow<Boolean> =
        context.dataStore.data.map { it[Keys.PROGRESS_NOTIFICATIONS] ?: true }

    val completedNotificationsEnabled: Flow<Boolean> =
        context.dataStore.data.map { it[Keys.COMPLETED_NOTIFICATIONS] ?: true }

    /** How many downloads DownloadWorker will let run at once (1-10, set via
     *  a slider in Settings). Re-read on every wait-loop poll in
     *  DownloadWorker, so changing it takes effect immediately for
     *  already-queued downloads too, not just new ones. */
    val maxConcurrentDownloads: Flow<Int> =
        context.dataStore.data.map { (it[Keys.MAX_CONCURRENT_DOWNLOADS] ?: 3).coerceIn(1, 10) }

    /** The clipboard link we last auto-prompted the user about (see
     *  BrowserViewModel.checkClipboardForVideoLink) -- persisted so we only
     *  prompt once per distinct copied link, not on every single app open. */
    val lastClipboardLinkPrompted: Flow<String?> =
        context.dataStore.data.map { it[Keys.LAST_CLIPBOARD_LINK] }

    suspend fun setLastClipboardLinkPrompted(url: String) {
        context.dataStore.edit { it[Keys.LAST_CLIPBOARD_LINK] = url }
    }

    suspend fun setThemeMode(mode: ThemeMode) {
        context.dataStore.edit { it[Keys.THEME_MODE] = mode.name }
    }

    suspend fun setWifiOnly(enabled: Boolean) {
        context.dataStore.edit { it[Keys.WIFI_ONLY] = enabled }
    }

    suspend fun setDownloadFolderUri(uri: String?) {
        context.dataStore.edit {
            if (uri == null) it.remove(Keys.DOWNLOAD_FOLDER_URI) else it[Keys.DOWNLOAD_FOLDER_URI] = uri
        }
    }

    suspend fun setAutoUpdateEnabled(enabled: Boolean) {
        context.dataStore.edit { it[Keys.AUTO_UPDATE_ENABLED] = enabled }
    }

    suspend fun setProgressNotificationsEnabled(enabled: Boolean) {
        context.dataStore.edit { it[Keys.PROGRESS_NOTIFICATIONS] = enabled }
    }

    suspend fun setCompletedNotificationsEnabled(enabled: Boolean) {
        context.dataStore.edit { it[Keys.COMPLETED_NOTIFICATIONS] = enabled }
    }

    suspend fun setMaxConcurrentDownloads(value: Int) {
        context.dataStore.edit { it[Keys.MAX_CONCURRENT_DOWNLOADS] = value.coerceIn(1, 10) }
    }

    suspend fun addCustomShortcut(shortcut: CustomShortcut) {
        context.dataStore.edit { prefs ->
            val current = parseShortcuts(prefs[Keys.CUSTOM_SHORTCUTS])
            prefs[Keys.CUSTOM_SHORTCUTS] = gson.toJson(current + shortcut)
        }
    }

    suspend fun removeCustomShortcut(shortcut: CustomShortcut) {
        context.dataStore.edit { prefs ->
            val current = parseShortcuts(prefs[Keys.CUSTOM_SHORTCUTS])
            val updated = current.filterNot { it.url == shortcut.url && it.label == shortcut.label }
            prefs[Keys.CUSTOM_SHORTCUTS] = gson.toJson(updated)
        }
    }

    private fun parseShortcuts(raw: String?): List<CustomShortcut> {
        if (raw.isNullOrBlank()) return emptyList()
        return runCatching { gson.fromJson<List<CustomShortcut>>(raw, customShortcutListType) }
            .getOrDefault(emptyList())
    }
}
