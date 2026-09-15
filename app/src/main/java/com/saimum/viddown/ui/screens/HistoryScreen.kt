package com.saimum.viddown.ui.screens

import android.content.ActivityNotFoundException
import android.content.Intent
import android.widget.Toast
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import com.saimum.viddown.data.DownloadEntity
import com.saimum.viddown.data.DownloadStatus
import com.saimum.viddown.download.DownloadRepository
import kotlinx.coroutines.launch
import java.io.File

@Composable
fun HistoryScreen() {
    val context = LocalContext.current
    val repo = remember(context) { DownloadRepository(context) }
    val history by repo.observeHistory().collectAsState(initial = emptyList())
    val scope = rememberCoroutineScope()

    if (history.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("Nothing downloaded yet")
        }
        return
    }

    LazyColumn(Modifier.fillMaxSize().padding(8.dp)) {
        items(history, key = { it.id }) { item ->
            HistoryRow(
                item = item,
                onDelete = { scope.launch { repo.delete(item.id) } }
            )
        }
    }
}

@Composable
private fun HistoryRow(item: DownloadEntity, onDelete: () -> Unit) {
    val context = LocalContext.current

    ListItem(
        headlineContent = { Text(item.title, maxLines = 1) },
        supportingContent = {
            Text(
                when (item.status) {
                    DownloadStatus.FAILED -> "Failed" + (item.errorMessage?.let { ": $it" } ?: "")
                    DownloadStatus.CANCELLED -> "Cancelled"
                    else -> item.formatLabel
                },
                style = MaterialTheme.typography.bodySmall
            )
        },
        trailingContent = {
            Row {
                if (item.status == DownloadStatus.COMPLETED) {
                    IconButton(onClick = { item.filePath?.let { openFile(context, it, item.isAudioOnly) } }) {
                        Icon(Icons.Filled.PlayArrow, contentDescription = "Play")
                    }
                    IconButton(onClick = { item.filePath?.let { shareFile(context, it, item.isAudioOnly) } }) {
                        Icon(Icons.Filled.Share, contentDescription = "Share")
                    }
                }
                IconButton(onClick = onDelete) {
                    Icon(Icons.Filled.Delete, contentDescription = "Delete")
                }
            }
        }
    )
    HorizontalDivider()
}

private fun uriFor(context: android.content.Context, path: String): android.net.Uri =
    if (path.startsWith("content://")) {
        android.net.Uri.parse(path)
    } else {
        FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", File(path))
    }

// BUG FIX: both functions below used to call context.startActivity(...)
// completely unguarded. If the device has no app that can handle the
// intent (no video/audio player installed, or -- for a content:// URI from
// a user-picked SAF folder -- no app registered for that mime type), Android
// throws ActivityNotFoundException and the *whole app crashes* with no
// warning. Wrapped so a missing handler app just shows a Toast instead of
// taking the app down. Also fixed the fallback mime type defaulting to
// "video/*" even for audio-only downloads (mp3/m4a), which could hide valid
// player apps from the chooser.
private fun openFile(context: android.content.Context, path: String, isAudioOnly: Boolean) {
    val uri = uriFor(context, path)
    val fallbackMime = if (isAudioOnly) "audio/*" else "video/*"
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(uri, context.contentResolver.getType(uri) ?: fallbackMime)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    try {
        context.startActivity(intent)
    } catch (e: ActivityNotFoundException) {
        Toast.makeText(context, "No app found to open this file", Toast.LENGTH_SHORT).show()
    }
}

private fun shareFile(context: android.content.Context, path: String, isAudioOnly: Boolean) {
    val uri = uriFor(context, path)
    val fallbackMime = if (isAudioOnly) "audio/*" else "video/*"
    val intent = Intent(Intent.ACTION_SEND).apply {
        type = context.contentResolver.getType(uri) ?: fallbackMime
        putExtra(Intent.EXTRA_STREAM, uri)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    try {
        context.startActivity(Intent.createChooser(intent, "Share via"))
    } catch (e: ActivityNotFoundException) {
        Toast.makeText(context, "No app found to share this file", Toast.LENGTH_SHORT).show()
    }
}
