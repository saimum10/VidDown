package com.saimum.viddown

import android.app.Application
import com.saimum.viddown.download.NotificationHelper
import com.saimum.viddown.engine.YtDlpEngine

class VidDownApp : Application() {
    override fun onCreate() {
        super.onCreate()
        // YtDlpEngine.init() dispatches the actual (slow) unpacking work to a
        // background thread internally and returns immediately -- see
        // YtDlpEngine.kt. It must never do that work here, synchronously, or
        // it blocks app startup on the main thread.
        YtDlpEngine.init(this)
        NotificationHelper.createChannel(this)
    }
}
