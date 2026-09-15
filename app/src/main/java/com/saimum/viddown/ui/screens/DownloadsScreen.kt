package com.saimum.viddown.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.saimum.viddown.data.DownloadEntity
import com.saimum.viddown.data.DownloadStatus
import com.saimum.viddown.download.DownloadRepository
import kotlinx.coroutines.launch

@Composable
fun DownloadsScreen() {
    val context = LocalContext.current
    val repo = remember(context) { DownloadRepository(context) }
    val active by repo.observeActive().collectAsState(initial = emptyList())
    val scope = rememberCoroutineScope()

    if (active.isEmpty()) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text("No active downloads")
        }
        return
    }

    LazyColumn(Modifier.fillMaxSize().padding(8.dp)) {
        items(active, key = { it.id }) { item ->
            ActiveDownloadRow(item, onCancel = { scope.launch { repo.cancel(item.id) } })
        }
    }
}

@Composable
private fun ActiveDownloadRow(item: DownloadEntity, onCancel: () -> Unit) {
    ListItem(
        headlineContent = { Text(item.title, maxLines = 1) },
        supportingContent = {
            Column {
                Text(item.formatLabel, style = MaterialTheme.typography.bodySmall)
                if (item.status == DownloadStatus.RUNNING) {
                    LinearProgressIndicator(
                        progress = { item.progressPercent.coerceIn(0, 100) / 100f },
                        modifier = Modifier.fillMaxWidth().padding(top = 4.dp)
                    )
                    Text("${item.progressPercent}%", style = MaterialTheme.typography.bodySmall)
                } else {
                    Text("Queued", style = MaterialTheme.typography.bodySmall)
                }
            }
        },
        trailingContent = {
            IconButton(onClick = onCancel) {
                Icon(Icons.Filled.Close, contentDescription = "Cancel")
            }
        }
    )
    HorizontalDivider()
}
