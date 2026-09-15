package com.saimum.viddown.ui.screens

import android.annotation.SuppressLint
import android.net.Uri
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.viewmodel.compose.viewModel
import com.saimum.viddown.data.CustomShortcut
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.ui.components.AddShortcutDialog
import com.saimum.viddown.ui.components.BrowserShortcut
import com.saimum.viddown.ui.components.DEFAULT_BROWSER_SHORTCUTS
import com.saimum.viddown.ui.components.FormatPickerSheet
import com.saimum.viddown.ui.components.HomeShortcutGrid
import com.saimum.viddown.ui.components.colorForCustomShortcut
import com.saimum.viddown.ui.viewmodel.BrowserViewModel
import kotlinx.coroutines.launch

@SuppressLint("SetJavaScriptEnabled")
@Composable
fun BrowserScreen(vm: BrowserViewModel = viewModel()) {
    val context = LocalContext.current
    val settingsRepo = remember(context) { SettingsRepository(context) }
    val scope = rememberCoroutineScope()

    var urlBarText by remember { mutableStateOf("") }
    var webViewRef by remember { mutableStateOf<WebView?>(null) }
    var showHomeGrid by remember { mutableStateOf(true) }
    var showAddDialog by remember { mutableStateOf(false) }
    val state by vm.state.collectAsState()
    val snackbarHostState = remember { SnackbarHostState() }

    // FIX (YouTube player not displaying properly): the WebView had no
    // WebChromeClient at all, so HTML5 <video> fullscreen requests (which is
    // how YouTube's player expands/plays on mobile) had nowhere to go --
    // onShowCustomView/onHideCustomView below is what makes that actually
    // work. customView holds the native player view the site hands us while
    // it's in fullscreen mode; null the rest of the time.
    var customView by remember { mutableStateOf<View?>(null) }
    var customViewCallback by remember { mutableStateOf<WebChromeClient.CustomViewCallback?>(null) }

    val customShortcuts by settingsRepo.customShortcuts.collectAsState(initial = emptyList())
    val shortcuts = remember(customShortcuts) {
        DEFAULT_BROWSER_SHORTCUTS + customShortcuts.map {
            BrowserShortcut(it.label, it.url, colorForCustomShortcut(it.url))
        }
    }

    // FIX: fetchFormats() (tapping the floating "Download" button) already
    // stopped the spinner on failure, but state.error was never actually
    // shown anywhere -- so a failed fetch (bad/unsupported link, no network,
    // nothing extractable) just looked like the tap did nothing at all.
    // This surfaces it and lets vm clear it once shown.
    LaunchedEffect(state.error) {
        state.error?.let { message ->
            snackbarHostState.showSnackbar(message)
            vm.clearError()
        }
    }

    // NEW: pop the quality picker automatically for a freshly-copied video
    // link, the way most downloader apps behave. checkClipboardForVideoLink
    // itself guards against re-prompting for the same link twice.
    LaunchedEffect(Unit) {
        vm.checkClipboardForVideoLink(context)
    }

    // Keep the url bar in sync with whatever link the auto-detect just
    // kicked off a fetch for, so it's not misleadingly blank/stale while the
    // picker is up (and so a subsequent manual Download tap uses the right
    // link too).
    LaunchedEffect(state.autoDetectedUrl) {
        state.autoDetectedUrl?.let { urlBarText = it }
    }

    // Home grid showing -> normal back exits like any top-level tab.
    // Browsing a page -> back steps the WebView back, or returns to the
    // home grid if there's nowhere left to go, instead of exiting outright.
    // Fullscreen video (customView != null) takes priority over both -- back
    // should collapse the video player first, same as a real browser.
    BackHandler(enabled = customView != null || !showHomeGrid) {
        if (customView != null) {
            customViewCallback?.onCustomViewHidden()
            customView = null
            customViewCallback = null
        } else if (webViewRef?.canGoBack() == true) {
            webViewRef?.goBack()
        } else {
            showHomeGrid = true
        }
    }

    fun openUrl(url: String) {
        showHomeGrid = false
        urlBarText = url
        webViewRef?.loadUrl(url)
    }

    Box(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
    Column(Modifier.fillMaxSize()) {
        if (showHomeGrid) {
            Text(
                "VidDown",
                style = MaterialTheme.typography.headlineMedium,
                fontWeight = FontWeight.Bold,
                color = MaterialTheme.colorScheme.onBackground,
                modifier = Modifier.padding(start = 16.dp, top = 16.dp, end = 16.dp, bottom = 4.dp)
            )
        }

        // FIX: this toolbar was invisible against a plain black background in
        // testing. It was wrapped in a Surface relying on tonalElevation to
        // pick its color, blended against colorScheme.surfaceTint -- which
        // this app's theme (Theme.kt) never sets, so it falls back to
        // Material3's baseline default (unrelated to this app's actual
        // palette) and evidently isn't resolving to anything visible here.
        // Rather than depend on that blend, this now paints an explicit,
        // fixed color plus explicit (not inherited) content colors below, so
        // there's no ambiguity about what actually gets drawn.
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier
                .fillMaxWidth()
                .background(MaterialTheme.colorScheme.surface)
                .padding(horizontal = 8.dp, vertical = 6.dp)
        ) {
            IconButton(onClick = { showHomeGrid = true }) {
                Icon(Icons.Filled.Home, contentDescription = "Home", tint = MaterialTheme.colorScheme.onSurface)
            }
            IconButton(
                enabled = !showHomeGrid,
                onClick = { webViewRef?.reload() }
            ) {
                Icon(
                    Icons.Filled.Refresh,
                    contentDescription = "Reload",
                    tint = if (!showHomeGrid) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurface.copy(alpha = 0.38f)
                )
            }
            // FIX (too tall / "chapta" thickness complaints): OutlinedTextField
            // has an internal minimum height (~56dp) that the outer Row's
            // padding never actually controlled -- that's why changing the
            // Row padding alone didn't change how thick this looked. This is
            // a compact custom field (fixed 40dp height) instead, so the
            // height is actually exact and adjustable.
            Box(
                modifier = Modifier
                    .weight(1f)
                    .padding(start = 4.dp)
                    .height(40.dp)
                    .clip(RoundedCornerShape(20.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .padding(horizontal = 12.dp),
                contentAlignment = Alignment.CenterStart
            ) {
                Icon(
                    Icons.Filled.Search,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(18.dp)
                )
                BasicTextField(
                    value = urlBarText,
                    onValueChange = { urlBarText = it },
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(start = 26.dp),
                    singleLine = true,
                    textStyle = LocalTextStyle.current.copy(
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        fontSize = 15.sp
                    ),
                    cursorBrush = SolidColor(MaterialTheme.colorScheme.onSurfaceVariant),
                    keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go),
                    keyboardActions = KeyboardActions(onGo = { openUrl(normalizeInput(urlBarText)) }),
                    decorationBox = { innerTextField ->
                        if (urlBarText.isEmpty()) {
                            Text(
                                "Search or paste a video link",
                                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
                                fontSize = 15.sp
                            )
                        }
                        innerTextField()
                    }
                )
            }
        }

        Box(Modifier.weight(1f)) {
            AndroidView(
                factory = { ctx ->
                    WebView(ctx).apply {
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        // FIX (most sites not displaying/loading properly):
                        // Android WebView can automatically apply "force
                        // dark" -- algorithmically inverting/recoloring page
                        // content -- when the app itself is in dark theme,
                        // for any site that doesn't declare its own dark
                        // mode support. This is a well-known cause of pages
                        // rendering broken, blank, or unreadable across many
                        // unrelated sites at once (exactly this symptom),
                        // not any actual load failure. Explicitly off.
                        @Suppress("DEPRECATION")
                        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                            settings.forceDark = WebSettings.FORCE_DARK_OFF
                        }
                        // FIX: without these, pages rendered at the wrong
                        // zoom/viewport, and sites like Facebook/Instagram/X
                        // that log in through an embedded/third-party cookie
                        // context could get stuck (silently failing to load
                        // past the login step) since third-party cookies
                        // were never explicitly allowed here.
                        settings.loadWithOverviewMode = true
                        settings.useWideViewPort = true
                        // FIX: without this, some sites' inline players stay
                        // stuck waiting for a gesture that never registers as
                        // "starting playback" from the WebView's point of
                        // view, leaving what looks like a broken/frozen player.
                        settings.mediaPlaybackRequiresUserGesture = false
                        // FIX ("Unable to parse video information" + the
                        // player rendering tiny/broken on YouTube): Android
                        // WebView's default user agent string always
                        // contains a literal "; wv" marker identifying the
                        // request as coming from an embedded WebView rather
                        // than a real browser. YouTube specifically checks
                        // for that and serves a stripped-down/inconsistent
                        // response to it (that's what the parse error and
                        // the squashed player layout actually are -- not a
                        // bug in this app's rendering, YouTube's own page is
                        // reacting to being detected). Presenting as a
                        // normal Chrome-for-Android user agent avoids that
                        // detection and gets the same page a real browser
                        // gets.
                        settings.userAgentString =
                            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 " +
                                "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36"
                        CookieManager.getInstance().setAcceptCookie(true)
                        CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
                        webViewClient = object : WebViewClient() {
                            override fun onPageFinished(view: WebView?, url: String?) {
                                super.onPageFinished(view, url)
                                url?.let {
                                    urlBarText = it
                                    vm.onPageLoaded(it)
                                }
                            }
                        }
                        // FIX (YouTube player not displaying properly): a
                        // plain WebView has no fullscreen concept of its own
                        // -- YouTube's mobile player expands into fullscreen
                        // via the standard HTML5 <video> fullscreen API,
                        // which only works if something implements
                        // onShowCustomView/onHideCustomView. Without this,
                        // that expand either silently does nothing or shows
                        // a broken/blank player area.
                        webChromeClient = object : WebChromeClient() {
                            override fun onShowCustomView(view: View?, callback: CustomViewCallback?) {
                                if (view == null) return
                                customView = view
                                customViewCallback = callback
                            }
                            override fun onHideCustomView() {
                                customViewCallback?.onCustomViewHidden()
                                customView = null
                                customViewCallback = null
                            }
                        }
                        webViewRef = this
                    }
                },
                modifier = Modifier.fillMaxSize()
            )

            if (showHomeGrid) {
                Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
                    HomeShortcutGrid(
                        shortcuts = shortcuts,
                        onOpen = { openUrl(it.url) },
                        onAddClick = { showAddDialog = true },
                        onRemove = { shortcut ->
                            scope.launch {
                                settingsRepo.removeCustomShortcut(CustomShortcut(shortcut.label, shortcut.url))
                            }
                        }
                    )
                }
            }

            if (!showHomeGrid && state.showDownloadFab) {
                ExtendedFloatingActionButton(
                    onClick = { vm.fetchFormats(urlBarText) },
                    icon = { Icon(Icons.Filled.Download, contentDescription = null) },
                    text = { Text("Download") },
                    modifier = Modifier
                        .align(Alignment.BottomEnd)
                        .padding(16.dp)
                )
            }

            if (state.isFetchingFormats) {
                CircularProgressIndicator(Modifier.align(Alignment.Center))
            }
        }
    }

    SnackbarHost(
        hostState = snackbarHostState,
        modifier = Modifier
            .align(Alignment.BottomCenter)
            .padding(16.dp)
    )

    // Hosts the fullscreen video view YouTube (etc.) hands us via
    // onShowCustomView, covering the whole screen while active. Kept as the
    // last child here so it's drawn on top of the toolbar/content/FAB.
    if (customView != null) {
        AndroidView(
            factory = { ctx -> FrameLayout(ctx) },
            update = { frame ->
                frame.removeAllViews()
                customView?.let {
                    (it.parent as? ViewGroup)?.removeView(it)
                    frame.addView(
                        it,
                        FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                    )
                }
            },
            modifier = Modifier
                .fillMaxSize()
                .background(androidx.compose.ui.graphics.Color.Black)
        )
    }
    }

    if (showAddDialog) {
        AddShortcutDialog(
            onDismiss = { showAddDialog = false },
            onConfirm = { label, url ->
                scope.launch { settingsRepo.addCustomShortcut(CustomShortcut(label, normalizeInput(url))) }
                showAddDialog = false
            }
        )
    }

    state.videoInfo?.let { info ->
        FormatPickerSheet(
            info = info,
            curatedOptions = state.curatedOptions,
            allOptions = state.allOptions,
            onDismiss = vm::dismissPicker,
            onPick = { option -> vm.startDownload(option) }
        )
    }
}

private fun normalizeInput(input: String): String = when {
    input.startsWith("http://") || input.startsWith("https://") -> input
    input.contains(".") && !input.contains(" ") -> "https://$input"
    else -> "https://www.google.com/search?q=${Uri.encode(input)}"
}
