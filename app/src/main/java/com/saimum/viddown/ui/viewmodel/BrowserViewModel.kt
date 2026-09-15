package com.saimum.viddown.ui.viewmodel

import android.app.Application
import android.content.ClipboardManager
import android.content.Context
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.saimum.viddown.data.SettingsRepository
import com.saimum.viddown.data.model.DownloadOption
import com.saimum.viddown.data.model.DownloadOptions
import com.saimum.viddown.data.model.VideoInfo
import com.saimum.viddown.download.DownloadRepository
import com.saimum.viddown.engine.CookieBridge
import com.saimum.viddown.engine.YtDlpEngine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

data class BrowserUiState(
    val showDownloadFab: Boolean = false,
    val isFetchingFormats: Boolean = false,
    val videoInfo: VideoInfo? = null,
    val curatedOptions: List<DownloadOption> = emptyList(),
    val allOptions: List<DownloadOption> = emptyList(),
    val error: String? = null,
    // BUG FIX: startDownload() used to take the url bar's *current* text at
    // pick-time. If the user fetched formats for link A, then edited the url
    // bar (without hitting Go) before picking a quality from the still-open
    // sheet, the download would silently start from whatever text was now
    // sitting in the bar instead of the video the formats/quality list
    // actually came from. This is set once, alongside videoInfo, and is what
    // startDownload() actually uses now.
    val sourceUrl: String? = null,
    // Set by checkClipboardForVideoLink() when a freshly-copied link should
    // be auto-fetched; BrowserScreen observes this once to sync the url bar
    // so the user can see what's being processed.
    val autoDetectedUrl: String? = null
)

class BrowserViewModel(app: Application) : AndroidViewModel(app) {

    private val repo = DownloadRepository(app)

    private val _state = MutableStateFlow(BrowserUiState())
    val state: StateFlow<BrowserUiState> = _state.asStateFlow()

    fun onPageLoaded(url: String) {
        viewModelScope.launch {
            val downloadable = YtDlpEngine.looksDownloadable(url)
            _state.value = _state.value.copy(showDownloadFab = downloadable)
        }
    }

    fun fetchFormats(url: String) {
        viewModelScope.launch {
            _state.value = _state.value.copy(isFetchingFormats = true, error = null, sourceUrl = url)
            val cookiesPath = withContext(Dispatchers.IO) {
                CookieBridge.exportCookiesForUrl(getApplication<Application>(), url)
            }
            val result = YtDlpEngine.extractInfo(url, cookiesPath)
            CookieBridge.delete(cookiesPath)
            _state.value = result.fold(
                onSuccess = { info ->
                    val (curated, all) = DownloadOptions.build(info)
                    _state.value.copy(
                        isFetchingFormats = false,
                        videoInfo = info,
                        curatedOptions = curated,
                        allOptions = all
                    )
                },
                onFailure = { e ->
                    _state.value.copy(
                        isFetchingFormats = false,
                        error = e.message?.takeIf { it.isNotBlank() } ?: "Couldn't read this link. Try a different page or video."
                    )
                }
            )
        }
    }

    fun clearError() {
        _state.value = _state.value.copy(error = null)
    }

    fun dismissPicker() {
        _state.value = _state.value.copy(videoInfo = null, curatedOptions = emptyList(), allOptions = emptyList(), sourceUrl = null)
    }

    fun startDownload(option: DownloadOption) {
        val info = _state.value.videoInfo ?: return
        val url = _state.value.sourceUrl ?: return
        viewModelScope.launch {
            repo.enqueue(url, info, option)
            _state.value = _state.value.copy(videoInfo = null, curatedOptions = emptyList(), allOptions = emptyList(), sourceUrl = null)
        }
    }

    /**
     * NEW: auto-detect a video link sitting on the clipboard and pop the
     * quality picker for it right away, the way most video-downloader apps
     * behave. Only fires once per distinct link (tracked in
     * SettingsRepository) so it doesn't re-prompt for the same copied link
     * every single time the app is opened.
     */
    fun checkClipboardForVideoLink(context: Context) {
        viewModelScope.launch {
            val clip = runCatching {
                val manager = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                manager?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.text?.toString()
            }.getOrNull()?.trim() ?: return@launch

            if (clip.isBlank() || !(clip.startsWith("http://") || clip.startsWith("https://")) || clip.contains(" ")) {
                return@launch
            }

            val settings = SettingsRepository(context)
            if (settings.lastClipboardLinkPrompted.first() == clip) return@launch
            settings.setLastClipboardLinkPrompted(clip)

            _state.value = _state.value.copy(autoDetectedUrl = clip)
            fetchFormats(clip)
        }
    }
}
