package com.saimum.viddown.engine

import android.content.Context
import android.net.Uri
import android.webkit.CookieManager
import java.io.File

/**
 * Bridges the in-app browser's login session into yt-dlp.
 *
 * BrowserScreen's WebView is where the user actually logs into sites --
 * without this, every extract/download hits those same URLs as a
 * logged-out, anonymous client and fails on anything that needs a session
 * (private posts, member-only videos, age-gated content, sites that just
 * block anonymous requests outright).
 *
 * android.webkit.CookieManager only exposes cookies as a flat
 * "name=value; name2=value2" request-header string, not full per-cookie
 * domain/path/expiry metadata, so this approximates a Netscape-format
 * cookies.txt (the format yt-dlp's `cookiefile` option expects) using the
 * request URL's own host/scheme and a long but finite expiry. That's the
 * accepted approach for exporting WebView cookies -- the platform API
 * simply doesn't expose anything more precise than that.
 */
object CookieBridge {

    /** Writes a Netscape cookies.txt for [url]'s cookies to the app cache
     *  and returns its path, or null if the WebView has none for it. */
    fun exportCookiesForUrl(context: Context, url: String): String? {
        val host = runCatching { Uri.parse(url).host }.getOrNull()
        if (host.isNullOrBlank()) return null

        val rawCookies = runCatching { CookieManager.getInstance().getCookie(url) }.getOrNull()
        if (rawCookies.isNullOrBlank()) return null

        val secure = url.startsWith("https://")
        val farExpiry = System.currentTimeMillis() / 1000 + 60L * 60 * 24 * 365 // ~1 year out

        val body = buildString {
            append("# Netscape HTTP Cookie File\n")
            rawCookies.split(";").forEach { pair ->
                val eq = pair.indexOf('=')
                if (eq <= 0) return@forEach
                val name = pair.substring(0, eq).trim()
                val value = pair.substring(eq + 1).trim()
                if (name.isEmpty()) return@forEach
                append(".$host\tTRUE\t/\t${if (secure) "TRUE" else "FALSE"}\t$farExpiry\t$name\t$value\n")
            }
        }

        return runCatching {
            val file = File(context.cacheDir, "cookies_${System.currentTimeMillis()}_${System.nanoTime()}.txt")
            file.writeText(body)
            file.absolutePath
        }.getOrNull()
    }

    /** Best-effort cleanup -- the file (session credentials!) only needs to
     *  live for the one extract or download call that requested it. */
    fun delete(path: String?) {
        if (path == null) return
        runCatching { File(path).delete() }
    }
}
