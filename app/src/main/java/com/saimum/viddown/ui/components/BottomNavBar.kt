package com.saimum.viddown.ui.components

import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.History
import androidx.compose.material.icons.filled.Language
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

sealed class Screen(val route: String, val label: String) {
    data object Browser : Screen("browser", "Browser")
    data object Downloads : Screen("downloads", "Downloads")
    data object History : Screen("history", "History")
    data object Settings : Screen("settings", "Settings")
    data object Updates : Screen("updates", "Updates")
    data object Notifications : Screen("notifications", "Notifications")
}

val bottomNavItems = listOf(Screen.Browser, Screen.Downloads, Screen.History, Screen.Settings)

@Composable
fun VidDownBottomBar(currentRoute: String?, onNavigate: (Screen) -> Unit) {
    NavigationBar {
        bottomNavItems.forEach { screen ->
            NavigationBarItem(
                selected = currentRoute == screen.route,
                onClick = { onNavigate(screen) },
                icon = {
                    Icon(
                        imageVector = when (screen) {
                            Screen.Browser -> Icons.Filled.Language
                            Screen.Downloads -> Icons.Filled.Download
                            Screen.History -> Icons.Filled.History
                            Screen.Settings -> Icons.Filled.Settings
                            Screen.Updates -> Icons.Filled.Settings
                            Screen.Notifications -> Icons.Filled.Settings
                        },
                        contentDescription = screen.label
                    )
                },
                label = { Text(screen.label) }
            )
        }
    }
}
