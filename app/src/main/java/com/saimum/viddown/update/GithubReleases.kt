package com.saimum.viddown.update

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject
import java.util.concurrent.TimeUnit

data class GithubAsset(val name: String, val downloadUrl: String)
data class GithubRelease(val tagName: String, val assets: List<GithubAsset>)

object GithubReleases {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()

    /** Returns null on any network/parsing failure -- callers treat that as "couldn't check". */
    suspend fun latest(repo: String): GithubRelease? = withContext(Dispatchers.IO) {
        runCatching {
            val request = Request.Builder()
                .url("https://api.github.com/repos/$repo/releases/latest")
                .header("Accept", "application/vnd.github+json")
                .build()
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) return@withContext null
                val body = resp.body?.string() ?: return@withContext null
                val json = JSONObject(body)
                val assetsArray = json.getJSONArray("assets")
                val assets = (0 until assetsArray.length()).map { i ->
                    val a = assetsArray.getJSONObject(i)
                    GithubAsset(a.getString("name"), a.getString("browser_download_url"))
                }
                GithubRelease(json.getString("tag_name"), assets)
            }
        }.getOrNull()
    }
}
