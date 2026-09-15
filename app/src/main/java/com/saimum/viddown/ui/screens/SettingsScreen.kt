package com.saimum.viddown.ui.screens

import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.saimum.viddown.BuildConfig
import com.saimum.viddown.download.DownloadRepository
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.data.ThemeMode
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@Composable
fun SettingsScreen(onOpenUpdates: () -> Unit, onOpenNotifications: () -> Unit) {
    val context = LocalContext.current
    val settings = remember(context) { SettingsRepository(context) }
    val downloadRepo = remember(context) { DownloadRepository(context) }
    val scope = rememberCoroutineScope()

    val themeMode by settings.themeMode.collectAsState(initial = ThemeMode.SYSTEM)
    val wifiOnly by settings.wifiOnly.collectAsState(initial = false)
    val folderUri by settings.downloadFolderUri.collectAsState(initial = null)
    val maxConcurrent by settings.maxConcurrentDownloads.collectAsState(initial = 3)

    var showThemeDialog by remember { mutableStateOf(false) }
    var showConcurrencyDialog by remember { mutableStateOf(false) }
    var showClearConfirm by remember { mutableStateOf(false) }
    var clearedMessage by remember { mutableStateOf<String?>(null) }

    val folderPicker = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) {
            context.contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
            scope.launch { settings.setDownloadFolderUri(uri.toString()) }
        }
    }

    LazyColumn(Modifier.fillMaxSize()) {
        item { SectionHeader("Downloads") }

        item {
            SettingsRow(
                icon = Icons.Filled.Folder,
                title = "Download location",
                subtitle = folderUri?.let { "Custom folder set" } ?: "App default (private storage)",
                onClick = { folderPicker.launch(null) }
            )
        }

        item {
            SettingsSwitchRow(
                icon = Icons.Filled.Wifi,
                title = "WiFi-only downloads",
                subtitle = "Pause new downloads on mobile data",
                checked = wifiOnly,
                onCheckedChange = { scope.launch { settings.setWifiOnly(it) } }
            )
        }

        item {
            SettingsRow(
                icon = Icons.Filled.Tune,
                title = "Max concurrent downloads",
                subtitle = "$maxConcurrent at a time",
                onClick = { showConcurrencyDialog = true }
            )
        }

        item { HorizontalDivider() }
        item { SectionHeader("Notifications") }

        item {
            SettingsRow(
                icon = Icons.Filled.Notifications,
                title = "Notifications",
                subtitle = "Download progress and completion alerts",
                onClick = onOpenNotifications,
                trailing = { Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null) }
            )
        }

        item { HorizontalDivider() }
        item { SectionHeader("Appearance") }

        item {
            SettingsRow(
                icon = Icons.Filled.Palette,
                title = "Theme",
                subtitle = when (themeMode) {
                    ThemeMode.SYSTEM -> "Follow system"
                    ThemeMode.LIGHT -> "Light"
                    ThemeMode.DARK -> "Dark"
                },
                onClick = { showThemeDialog = true }
            )
        }

        item { HorizontalDivider() }
        item { SectionHeader("Storage") }

        item {
            SettingsRow(
                icon = Icons.Filled.DeleteSweep,
                title = "Clear history",
                subtitle = "Clears the History list (downloaded files are kept)",
                onClick = { showClearConfirm = true }
            )
        }

        item { HorizontalDivider() }
        item { SectionHeader("Updates") }

        item {
            SettingsRow(
                icon = Icons.Filled.SystemUpdate,
                title = "App & tool updates",
                subtitle = "Check for a newer VidDown build, yt-dlp, ffmpeg, aria2c",
                onClick = onOpenUpdates,
                trailing = { Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = null) }
            )
        }

        item { HorizontalDivider() }
        item { SectionHeader("About") }

        item {
            ListItem(
                headlineContent = { Text("VidDown") },
                supportingContent = { Text("Version ${BuildConfig.VERSION_NAME} (build ${BuildConfig.VERSION_CODE})") },
                leadingContent = { Icon(Icons.Filled.Info, contentDescription = null) }
            )
        }

        item {
            SettingsRow(
                icon = Icons.Filled.Code,
                title = "GitHub",
                subtitle = "github.com/saimum10/VidDown",
                onClick = {
                    val intent = Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/saimum10/VidDown"))
                    try {
                        context.startActivity(intent)
                    } catch (e: ActivityNotFoundException) {
                        Toast.makeText(context, "No browser app found", Toast.LENGTH_SHORT).show()
                    }
                },
                trailing = { Icon(Icons.Filled.OpenInNew, contentDescription = null) }
            )
        }
    }

    if (showThemeDialog) {
        ThemePickerDialog(
            current = themeMode,
            onDismiss = { showThemeDialog = false },
            onPick = { mode ->
                scope.launch { settings.setThemeMode(mode) }
                showThemeDialog = false
            }
        )
    }

    if (showConcurrencyDialog) {
        MaxConcurrentDialog(
            current = maxConcurrent,
            onDismiss = { showConcurrencyDialog = false },
            onPick = { value ->
                scope.launch { settings.setMaxConcurrentDownloads(value) }
                showConcurrencyDialog = false
            }
        )
    }

    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("Clear history?") },
            text = { Text("This clears the History list. Already-downloaded files on your device are not deleted.") },
            confirmButton = {
                TextButton(onClick = {
                    showClearConfirm = false
                    scope.launch {
                        downloadRepo.clearHistory()
                        clearedMessage = "Cleared"
                    }
                }) { Text("Clear") }
            },
            dismissButton = { TextButton(onClick = { showClearConfirm = false }) { Text("Cancel") } }
        )
    }

    clearedMessage?.let {
        LaunchedEffect(it) {
            kotlinx.coroutines.delay(1500)
            clearedMessage = null
        }
    }
}

@Composable
private fun ThemePickerDialog(current: ThemeMode, onDismiss: () -> Unit, onPick: (ThemeMode) -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Theme") },
        text = {
            Column {
                ThemeMode.entries.forEach { mode ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        RadioButton(selected = mode == current, onClick = { onPick(mode) })
                        Spacer(Modifier.width(8.dp))
                        Text(
                            when (mode) {
                                ThemeMode.SYSTEM -> "Follow system"
                                ThemeMode.LIGHT -> "Light"
                                ThemeMode.DARK -> "Dark"
                            }
                        )
                    }
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text("Close") } }
    )
}

@Composable
private fun MaxConcurrentDialog(current: Int, onDismiss: () -> Unit, onPick: (Int) -> Unit) {
    var value by remember(current) { mutableIntStateOf(current) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Max concurrent downloads") },
        text = {
            Column {
                Text("$value at a time", style = MaterialTheme.typography.titleMedium)
                Slider(
                    value = value.toFloat(),
                    onValueChange = { value = it.roundToInt() },
                    valueRange = 1f..10f,
                    steps = 8
                )
                Text(
                    "Downloads beyond this limit wait in the queue until a slot frees up.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        },
        confirmButton = { TextButton(onClick = { onPick(value) }) { Text("Save") } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Cancel") } }
    )
}

@Composable
private fun SectionHeader(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(16.dp, 16.dp, 16.dp, 4.dp)
    )
}

@Composable
private fun SettingsRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    onClick: () -> Unit,
    trailing: (@Composable () -> Unit)? = null
) {
    ListItem(
        headlineContent = { Text(title) },
        supportingContent = { Text(subtitle) },
        leadingContent = { Icon(icon, contentDescription = null) },
        trailingContent = trailing,
        modifier = Modifier.clickable { onClick() }
    )
}

@Composable
private fun SettingsSwitchRow(
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    title: String,
    subtitle: String,
    checked: Boolean,
    onCheckedChange: (Boolean) -> Unit
) {
    ListItem(
        headlineContent = { Text(title) },
        supportingContent = { Text(subtitle) },
        leadingContent = { Icon(icon, contentDescription = null) },
        trailingContent = { Switch(checked = checked, onCheckedChange = onCheckedChange) }
    )
}
