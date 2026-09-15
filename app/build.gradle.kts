plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("com.google.devtools.ksp")
}

android {
    namespace = "com.saimum.viddown"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.saimum.viddown"
        minSdk = 29
        targetSdk = 34
        versionCode = 1
        versionName = "2.9.0"

        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        ndk {
            // Matches the ABIs youtubedl-android ships prebuilt Python/yt-dlp/ffmpeg/aria2c for.
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    // FIX: this used to split into a separate APK per ABI (+ a universal
    // one), and CI uploaded all of them to the GitHub Release with no
    // obvious labeling of which one to actually install. yt-dlp/ffmpeg/
    // aria2c are bundled as real per-ABI native executables (see the
    // dependency comment below) -- installing the wrong-ABI APK for a given
    // device still installs fine, but those binaries then simply don't run
    // on that device's CPU, so every extraction/download silently fails,
    // which looks exactly like "can't detect any link" no matter what's
    // pasted in. Disabled while actively debugging so every build produces
    // exactly one APK with all three ABIs bundled in -- nothing to pick
    // wrong. Worth re-enabling later for Play Store distribution, once
    // everything's confirmed working, to keep download sizes down.
    splits {
        abi {
            isEnable = false
        }
    }

    signingConfigs {
        create("release") {
            val keystorePath = System.getenv("VIDDOWN_KEYSTORE_PATH")
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("VIDDOWN_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("VIDDOWN_KEY_ALIAS")
                keyPassword = System.getenv("VIDDOWN_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (System.getenv("VIDDOWN_KEYSTORE_PATH") != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
        debug {
            applicationIdSuffix = ".debug"
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources {
            excludes += "/META-INF/{AL2.0,LGPL2.1}"
        }
        // youtubedl-android ships its Python/yt-dlp/ffmpeg/aria2c payloads as
        // fake "lib*.so" files so the Android packager extracts them to
        // nativeLibraryDir at install time (they're real executables, not
        // actual shared libraries -- see AndroidManifest's extractNativeLibs).
        jniLibs {
            useLegacyPackaging = true
        }
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("androidx.activity:activity-compose:1.9.2")

    implementation(platform("androidx.compose:compose-bom:2024.09.02"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-graphics")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")
    debugImplementation("androidx.compose.ui:ui-tooling")

    implementation("androidx.navigation:navigation-compose:2.8.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    // Background downloads + notifications
    implementation("androidx.work:work-runtime-ktx:2.9.1")

    // Local history / queue persistence
    implementation("androidx.room:room-runtime:2.6.1")
    implementation("androidx.room:room-ktx:2.6.1")
    ksp("androidx.room:room-compiler:2.6.1")

    implementation("com.google.code.gson:gson:2.11.0")
    implementation("androidx.datastore:datastore-preferences:1.1.1")
    implementation("androidx.documentfile:documentfile:1.0.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")

    // Bundles yt-dlp + a Python runtime as a prebuilt, size-optimized AAR
    // per ABI (replaces the old Chaquopy + pip yt-dlp setup). ffmpeg is
    // included unconditionally -- the library always passes
    // --ffmpeg-location on every execute() call, so audio extraction/remux
    // silently breaks without it even though upstream's own README doesn't
    // mark it required. aria2c is genuinely optional (only engaged when a
    // request explicitly asks for the "libaria2c.so" downloader) -- kept in,
    // matching ytdlnis's own default ("github") build flavor.
    implementation("io.github.junkfood02.youtubedl-android:library:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:ffmpeg:0.18.1")
    implementation("io.github.junkfood02.youtubedl-android:aria2c:0.18.1")

    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.espresso:espresso-core:3.6.1")
}
