package com.saimum.viddown.update

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.content.FileProvider
import com.saimum.viddown.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.TimeUnit

data class AppUpdateInfo(val latestVersion: String, val downloadUrl: String?, val updateAvailable: Boolean)

class AppUpdateChecker(private val context: Context) {

    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun currentAbi(): String =
        Build.SUPPORTED_ABIS.firstOrNull { it in listOf("arm64-v8a", "armeabi-v7a", "x86_64") } ?: "universal"

    suspend fun check(): AppUpdateInfo? = withContext(Dispatchers.IO) {
        val release = GithubReleases.latest(UpdateRepos.APP_REPO) ?: return@withContext null
        val latest = release.tagName.removePrefix("v")
        val current = BuildConfig.VERSION_NAME

        val abi = currentAbi()
        val asset = release.assets.firstOrNull { it.name.contains(abi) && it.name.endsWith(".apk") }
            ?: release.assets.firstOrNull { it.name.contains("universal") && it.name.endsWith(".apk") }

        AppUpdateInfo(
            latestVersion = latest,
            downloadUrl = asset?.downloadUrl,
            updateAvailable = latest != current
        )
    }

    /** Downloads the APK and hands it to the system installer. The user still
     *  confirms the actual install -- this app never installs anything silently. */
    suspend fun downloadAndLaunchInstaller(downloadUrl: String): Result<Unit> = withContext(Dispatchers.IO) {
        runCatching {
            val apkFile = File(context.cacheDir, "viddown-update.apk")
            val request = Request.Builder().url(downloadUrl).build()
            client.newCall(request).execute().use { resp ->
                if (!resp.isSuccessful) throw IllegalStateException("Download failed: HTTP ${resp.code}")
                resp.body?.byteStream()?.use { input ->
                    FileOutputStream(apkFile).use { output -> input.copyTo(output) }
                } ?: throw IllegalStateException("Empty response body")
            }

            val uri: Uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", apkFile)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(intent)
        }
    }
}
