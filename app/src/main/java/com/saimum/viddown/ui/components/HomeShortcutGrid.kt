package com.saimum.viddown.ui.components

import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlin.math.abs

/** One tile on the Browser tab's home grid. */
data class BrowserShortcut(
    val label: String,
    val url: String,
    val colorHex: Long,
    val isDefault: Boolean = false
)

private val PALETTE: List<Long> = listOf(
    0xFF6750A4, 0xFF386641, 0xFFBB4D00, 0xFF006A6A,
    0xFF984061, 0xFF1B5E20, 0xFF283593, 0xFF8E24AA
)

/** Deterministic-but-varied tile color for a user-added shortcut, since we
 *  don't fetch real favicons here -- keyed off the URL so the same site
 *  always lands on the same color across app restarts. */
fun colorForCustomShortcut(url: String): Long = PALETTE[abs(url.hashCode()) % PALETTE.size]

val DEFAULT_BROWSER_SHORTCUTS = listOf(
    BrowserShortcut("YouTube", "https://www.youtube.com", 0xFFFF0000, isDefault = true),
    BrowserShortcut("Facebook", "https://www.facebook.com", 0xFF1877F2, isDefault = true),
    BrowserShortcut("X", "https://x.com", 0xFF000000, isDefault = true),
    BrowserShortcut("Instagram", "https://www.instagram.com", 0xFFC13584, isDefault = true),
)

@OptIn(ExperimentalFoundationApi::class)
@Composable
fun HomeShortcutGrid(
    shortcuts: List<BrowserShortcut>,
    onOpen: (BrowserShortcut) -> Unit,
    onAddClick: () -> Unit,
    onRemove: (BrowserShortcut) -> Unit,
    modifier: Modifier = Modifier
) {
    LazyVerticalGrid(
        columns = GridCells.Fixed(4),
        modifier = modifier.fillMaxSize(),
        contentPadding = PaddingValues(20.dp),
        horizontalArrangement = Arrangement.spacedBy(12.dp),
        verticalArrangement = Arrangement.spacedBy(20.dp)
    ) {
        items(shortcuts, key = { it.url + it.label }) { shortcut ->
            ShortcutTile(
                shortcut = shortcut,
                onClick = { onOpen(shortcut) },
                onLongClick = { if (!shortcut.isDefault) onRemove(shortcut) }
            )
        }
        item(key = "add_shortcut") {
            AddShortcutTile(onClick = onAddClick)
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun ShortcutTile(shortcut: BrowserShortcut, onClick: () -> Unit, onLongClick: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.combinedClickable(onClick = onClick, onLongClick = onLongClick)
    ) {
        Box(
            modifier = Modifier
                .size(56.dp)
                .clip(RoundedCornerShape(16.dp))
                .background(Color(shortcut.colorHex))
        ) {
            Text(
                shortcut.label.take(1).uppercase(),
                color = Color.White,
                fontWeight = FontWeight.Bold,
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.align(Alignment.Center)
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(shortcut.label, style = MaterialTheme.typography.bodySmall, maxLines = 1)
    }
}

@Composable
private fun AddShortcutTile(onClick: () -> Unit) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier.clickable(onClick = onClick)
    ) {
        Box(
            modifier = Modifier
                .size(56.dp)
                .clip(RoundedCornerShape(16.dp))
                .border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(16.dp))
        ) {
            Icon(Icons.Filled.Add, contentDescription = "Add shortcut", modifier = Modifier.align(Alignment.Center))
        }
        Spacer(Modifier.height(4.dp))
        Text("Add", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
fun AddShortcutDialog(onDismiss: () -> Unit, onConfirm: (label: String, url: String) -> Unit) {
    var name by remember { mutableStateOf("") }
    var url by remember { mutableStateOf("") }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add shortcut") },
        text = {
            Column {
                OutlinedTextField(
                    value = name,
                    onValueChange = { name = it },
                    label = { Text("Name") },
                    singleLine = true
                )
                Spacer(Modifier.height(8.dp))
                OutlinedTextField(
                    value = url,
                    onValueChange = { url = it },
                    label = { Text("Website URL") },
                    singleLine = true
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(name.ifBlank { url }, url) }, enabled = url.isNotBlank()) {
                Text("Add")
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        }
    )
}
