package com.saimum.viddown.ui.screens

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.saimum.viddown.data.SettingsRepository
import kotlinx.coroutines.launch

@Composable
fun NotificationsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val settings = remember(context) { SettingsRepository(context) }
    val scope = rememberCoroutineScope()

    val progressEnabled by settings.progressNotificationsEnabled.collectAsState(initial = true)
    val completedEnabled by settings.completedNotificationsEnabled.collectAsState(initial = true)

    Column(Modifier.fillMaxSize()) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 4.dp, vertical = 8.dp)
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.Filled.ArrowBack, contentDescription = "Back")
            }
            Text("Notifications", style = MaterialTheme.typography.titleLarge)
        }

        LazyColumn(Modifier.fillMaxSize()) {
            item {
                Text(
                    "Download notifications",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(16.dp, 16.dp, 16.dp, 4.dp)
                )
            }
            item {
                ListItem(
                    headlineContent = { Text("Download progress") },
                    supportingContent = { Text("Notify me of download progress") },
                    trailingContent = {
                        Switch(
                            checked = progressEnabled,
                            onCheckedChange = { scope.launch { settings.setProgressNotificationsEnabled(it) } }
                        )
                    }
                )
            }
            item {
                ListItem(
                    headlineContent = { Text("Download completed") },
                    supportingContent = { Text("Notify me when download is complete") },
                    trailingContent = {
                        Switch(
                            checked = completedEnabled,
                            onCheckedChange = { scope.launch { settings.setCompletedNotificationsEnabled(it) } }
                        )
                    }
                )
            }
        }
    }
}
