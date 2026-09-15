package com.saimum.viddown.ui.components

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Movie
import androidx.compose.material.icons.filled.MusicNote
import androidx.compose.material.icons.outlined.RadioButtonUnchecked
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.saimum.viddown.data.model.DownloadOption
import com.saimum.viddown.data.model.VideoInfo

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FormatPickerSheet(
    info: VideoInfo,
    curatedOptions: List<DownloadOption>,
    allOptions: List<DownloadOption>,
    onDismiss: () -> Unit,
    onPick: (DownloadOption) -> Unit
) {
    var showAll by remember { mutableStateOf(false) }
    var selected by remember(curatedOptions) {
        mutableStateOf(
            curatedOptions.firstOrNull { it is DownloadOption.Video && it.heightCap == 240 }
                ?: curatedOptions.firstOrNull()
        )
    }
    val visibleOptions = if (showAll) allOptions else curatedOptions
    val audioOptions = visibleOptions.filterIsInstance<DownloadOption.Audio>()
    val videoOptions = visibleOptions.filterIsInstance<DownloadOption.Video>()

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(Modifier.padding(bottom = 16.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                if (showAll) {
                    IconButton(onClick = { showAll = false }) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "Back")
                    }
                    Text("More formats", style = MaterialTheme.typography.titleMedium)
                } else {
                    Text("Download video as", style = MaterialTheme.typography.titleMedium, maxLines = 1)
                }
            }

            LazyColumn(modifier = Modifier.heightIn(max = 420.dp)) {
                if (audioOptions.isNotEmpty()) {
                    item { SectionHeader("Music") }
                    items(audioOptions) { option ->
                        DownloadOptionRow(option, selected == option) { selected = option }
                    }
                }
                if (videoOptions.isNotEmpty()) {
                    item { SectionHeader("Video") }
                    items(videoOptions) { option ->
                        DownloadOptionRow(option, selected == option) { selected = option }
                    }
                }
            }

            if (!showAll) {
                HorizontalDivider()
                ListItem(
                    modifier = Modifier.clickable { showAll = true },
                    headlineContent = { Text("More formats") },
                    trailingContent = {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text("All", color = MaterialTheme.colorScheme.onSurfaceVariant)
                            Icon(Icons.Filled.KeyboardArrowRight, contentDescription = null)
                        }
                    }
                )
            }

            Button(
                onClick = { selected?.let(onPick) },
                enabled = selected != null,
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 8.dp)
            ) {
                Text("Download")
            }
        }
    }
}

@Composable
private fun SectionHeader(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp)
    )
}

@Composable
private fun DownloadOptionRow(option: DownloadOption, selected: Boolean, onSelect: () -> Unit) {
    ListItem(
        modifier = Modifier.clickable(onClick = onSelect),
        leadingContent = {
            Icon(
                imageVector = if (option is DownloadOption.Video) Icons.Filled.Movie else Icons.Filled.MusicNote,
                contentDescription = null
            )
        },
        headlineContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(option.label)
                option.badge?.let { badge ->
                    Spacer(Modifier.width(6.dp))
                    Surface(
                        shape = RoundedCornerShape(4.dp),
                        color = MaterialTheme.colorScheme.secondaryContainer
                    ) {
                        Text(
                            badge,
                            style = MaterialTheme.typography.labelSmall,
                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp)
                        )
                    }
                }
            }
        },
        supportingContent = option.subtitle?.let { subtitle ->
            { Text(subtitle, style = MaterialTheme.typography.bodySmall) }
        },
        trailingContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                option.approxSizeBytes?.let {
                    Text(
                        formatSize(it),
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.padding(end = 8.dp)
                    )
                }
                if (selected) {
                    Icon(
                        Icons.Filled.CheckCircle,
                        contentDescription = "Selected",
                        tint = MaterialTheme.colorScheme.primary
                    )
                } else {
                    Icon(
                        Icons.Outlined.RadioButtonUnchecked,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.outline
                    )
                }
            }
        }
    )
    HorizontalDivider()
}

private fun formatSize(bytes: Long): String {
    val mb = bytes / (1024.0 * 1024.0)
    return if (mb < 0.1) "%.0f KB".format(bytes / 1024.0) else "%.1f MB".format(mb)
}
