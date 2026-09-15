package com.saimum.viddown.ui.screens

import android.content.Context
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.saimum.viddown.BuildConfig
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.engine.YtDlpEngine
import com.saimum.viddown.update.AppUpdateChecker
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun UpdatesScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val settings = remember(context) { SettingsRepository(context) }
    val appChecker = remember(context) { AppUpdateChecker(context) }
    val scope = rememberCoroutineScope()

    val autoUpdate by settings.autoUpdateEnabled.collectAsState(initial = true)

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Updates") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                }
            )
        }
    ) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            ListItem(
                headlineContent = { Text("Auto-check for updates") },
                supportingContent = { Text("Check on startup (never installs without asking)") },
                trailingContent = {
                    Switch(
                        checked = autoUpdate,
                        onCheckedChange = { scope.launch { settings.setAutoUpdateEnabled(it) } }
                    )
                }
            )
            HorizontalDivider()

            AppUpdateRow(appChecker)
            HorizontalDivider()

            // ffmpeg and aria2c no longer have update rows here -- they're
            // bundled at build time via Gradle (youtubedl-android AARs), not
            // hot-swappable binaries fetched at runtime like before.
            // "Updating" them now just means bumping the dependency version
            // in build.gradle.kts and shipping a new app release.
            YtDlpUpdateRow(context)
        }
    }
}

@Composable
private fun AppUpdateRow(checker: AppUpdateChecker) {
    val scope = rememberCoroutineScope()
    var status by remember { mutableStateOf("Version ${BuildConfig.VERSION_NAME}") }
    var updateUrl by remember { mutableStateOf<String?>(null) }
    var isBusy by remember { mutableStateOf(false) }

    ListItem(
        headlineContent = { Text("VidDown app") },
        supportingContent = { Text(status) },
        trailingContent = {
            Row {
                TextButton(
                    enabled = !isBusy,
                    onClick = {
                        isBusy = true
                        scope.launch {
                            val info = checker.check()
                            isBusy = false
                            status = when {
                                info == null -> "Couldn't check — no network or repo not set up"
                                info.updateAvailable -> "Update available: v${info.latestVersion}"
                                else -> "Up to date (v${info.latestVersion})"
                            }
                            updateUrl = if (info?.updateAvailable == true) info.downloadUrl else null
                        }
                    }
                ) { Text("Check") }

                if (updateUrl != null) {
                    TextButton(
                        enabled = !isBusy,
                        onClick = {
                            val url = updateUrl ?: return@TextButton
                            isBusy = true
                            scope.launch {
                                val result = checker.downloadAndLaunchInstaller(url)
                                isBusy = false
                                status = if (result.isSuccess) "Installer opened" else "Download failed"
                            }
                        }
                    ) { Text("Update") }
                }
            }
        }
    )
}

/**
 * youtubedl-android's updater checks-and-updates in one step (there's no
 * separate "peek at latest version" call in the library), so unlike the old
 * ffmpeg/aria2c rows this is a single button rather than Check-then-Update.
 */
@Composable
private fun YtDlpUpdateRow(context: Context) {
    val scope = rememberCoroutineScope()
    var status by remember {
        mutableStateOf(
            YtDlpEngine.currentYtDlpVersion(context)?.let { "Installed: $it" }
                ?: "Bundled version (not yet updated)"
        )
    }
    var isBusy by remember { mutableStateOf(false) }

    ListItem(
        headlineContent = { Text("yt-dlp") },
        supportingContent = { Text(status) },
        trailingContent = {
            TextButton(
                enabled = !isBusy,
                onClick = {
                    isBusy = true
                    scope.launch {
                        val result = YtDlpEngine.updateYtDlp(context)
                        isBusy = false
                        status = when {
                            !result.success -> "Update failed: ${result.error}"
                            result.error == "Already up to date" -> "Already up to date (${result.versionName ?: "unknown"})"
                            else -> "Updated to ${result.versionName ?: "latest"}"
                        }
                    }
                }
            ) { Text("Update") }
        }
    )
}
