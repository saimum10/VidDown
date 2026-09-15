package com.saimum.viddown

import android.Manifest
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.data.ThemeMode
import com.saimum.viddown.ui.components.Screen
import com.saimum.viddown.ui.components.VidDownBottomBar
import com.saimum.viddown.ui.screens.BrowserScreen
import com.saimum.viddown.ui.screens.DownloadsScreen
import com.saimum.viddown.ui.screens.HistoryScreen
import com.saimum.viddown.ui.screens.NotificationsScreen
import com.saimum.viddown.ui.screens.SettingsScreen
import com.saimum.viddown.ui.screens.UpdatesScreen
import com.saimum.viddown.ui.theme.VidDownTheme

class MainActivity : ComponentActivity() {

    private val notificationPermissionLauncher =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { /* no-op either way */ }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        setContent {
            val context = LocalContext.current
            val settings = remember(context) { SettingsRepository(context) }
            val themeMode by settings.themeMode.collectAsState(initial = ThemeMode.SYSTEM)

            VidDownTheme(themeMode = themeMode) {
                VidDownRoot()
            }
        }
    }
}

@Composable
private fun VidDownRoot() {
    val navController = rememberNavController()
    Scaffold(
        bottomBar = {
            val backStackEntry by navController.currentBackStackEntryAsState()
            val currentRoute = backStackEntry?.destination?.route
            // The Updates and Notifications screens are pushed on top of
            // Settings, not bottom-nav destinations themselves -- keep the
            // Settings tab highlighted while on either of them.
            val highlightedRoute = if (currentRoute == Screen.Updates.route || currentRoute == Screen.Notifications.route) {
                Screen.Settings.route
            } else {
                currentRoute
            }
            VidDownBottomBar(
                currentRoute = highlightedRoute,
                onNavigate = { screen -> navigateSingleTop(navController, screen) }
            )
        }
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Screen.Browser.route,
            modifier = Modifier.padding(padding)
        ) {
            composable(Screen.Browser.route) { BrowserScreen() }
            composable(Screen.Downloads.route) { DownloadsScreen() }
            composable(Screen.History.route) { HistoryScreen() }
            composable(Screen.Settings.route) {
                SettingsScreen(
                    onOpenUpdates = { navController.navigate(Screen.Updates.route) },
                    onOpenNotifications = { navController.navigate(Screen.Notifications.route) }
                )
            }
            composable(Screen.Updates.route) {
                UpdatesScreen(onBack = { navController.popBackStack() })
            }
            composable(Screen.Notifications.route) {
                NotificationsScreen(onBack = { navController.popBackStack() })
            }
        }
    }
}

private fun navigateSingleTop(nav: NavHostController, screen: Screen) {
    nav.navigate(screen.route) {
        popUpTo(nav.graph.startDestinationId) { saveState = true }
        launchSingleTop = true
        restoreState = true
    }
}
