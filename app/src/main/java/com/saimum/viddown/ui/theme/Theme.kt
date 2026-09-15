package com.saimum.viddown.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import com.saimum.viddown.data.ThemeMode

private val DarkColors = darkColorScheme(
    primary = VidDownBlueLight,
    secondary = VidDownBlue,
    background = VidDownBgDark,
    surface = VidDownSurfaceDark
)

private val LightColors = lightColorScheme(
    primary = VidDownBlue,
    secondary = VidDownBlueDark,
)

@Composable
fun VidDownTheme(
    themeMode: ThemeMode = ThemeMode.SYSTEM,
    content: @Composable () -> Unit
) {
    val darkTheme = when (themeMode) {
        ThemeMode.SYSTEM -> isSystemInDarkTheme()
        ThemeMode.LIGHT -> false
        ThemeMode.DARK -> true
    }
    val colors = if (darkTheme) DarkColors else LightColors
    MaterialTheme(colorScheme = colors, content = content)
}
